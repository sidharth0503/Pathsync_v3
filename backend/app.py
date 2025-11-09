import os
import sys
import subprocess
import time
from flask import Flask, jsonify, request
from flask_cors import CORS 
from geopy.geocoders import Nominatim 
import threading
import networkx as nx
import traci
import sumolib 

# --- 1. CONFIGURE FLASK APP ---
app = Flask(__name__)
CORS(app) 
geolocator = Nominatim(user_agent="pathsync-v3-router")
# -----------------------------

# --- 2. FIND AND IMPORT TRACI / SUMOLIB ---
if 'SUMO_HOME' in os.environ:
    tools = os.path.join(os.environ['SUMO_HOME'], 'tools')
    sys.path.append(tools)
else:
    sys.exit("please declare 'SUMO_HOME' as an environment variable")

# --- 3. DEFINE SUMO COMMAND ---
sumoBinary = "sumo" # FINAL: Run Headless for maximum speed
sumo_net_file = "simulation/map.net.xml" 
sumoConfig = "simulation/map.sumocfg"

sumoCmd = [
    sumoBinary,
    "-c", sumoConfig,
    "--end", "3600" # Must match the 1-hour simulation length
]

# --- 6. LOAD/CREATE YOUR ROUTING GRAPH ---
print("Loading SUMO map into NetworkX graph...")

net = sumolib.net.readNet(sumo_net_file)
G = nx.MultiDiGraph() 
edge_id_to_uv = {} # Lookup map for O(n) efficiency

# Add all nodes
for node in net.getNodes():
    node_id = node.getID()
    x, y = node.getCoord()
    lon, lat = net.convertXY2LonLat(x, y)
    G.add_node(node_id, x=x, y=y, lon=lon, lat=lat)

# Add all edges
for edge in net.getEdges():
    edge_id = edge.getID()
    from_node = edge.getFromNode().getID()
    to_node = edge.getToNode().getID()
    length = edge.getLength()
    speed = edge.getSpeed()
    travel_time = length / speed
    
    G.add_edge(from_node, to_node, key=edge_id, length=length, travel_time=travel_time)
    edge_id_to_uv[edge_id] = (from_node, to_node)

print("NetworkX graph 'G' created successfully from SUMO net.")


# --- 5. DEFINE "AI ENGINE" HEARTBEAT FUNCTION ---
def update_live_traffic():
    """
    The main "AI Engine" loop. Constantly updates graph with live travel times.
    """
    print("AI Engine: Background thread started. Attempting to launch and connect...")
    
    try:
        # Traci handles remote-port and num-clients internally
        traci.start(sumoCmd, port=8813)
        print("AI Engine: SUMO started and Traci connected successfully.")
        
    except Exception as e:
        print(f"AI Engine: Failed to launch/connect to Traci: {e}")
        return

    # --- This is the main loop ---
    while True:
        try:
            traci.simulationStep()
            current_time = traci.simulation.getTime()

            # --- DIGITAL TWIN LOGIC (O(n) efficient) ---
            all_sumo_edges = traci.edge.getIDList()
            
            for edge_id in all_sumo_edges:
                current_travel_time = traci.edge.getTraveltime(edge_id) 
                
                if edge_id in edge_id_to_uv:
                    u, v = edge_id_to_uv[edge_id]
                    # This check ensures that reported incidents (is_incident=True) are NOT overwritten
                    if G.edges[u, v, edge_id].get('is_incident') != True:
                        G.edges[u, v, edge_id]['travel_time'] = current_travel_time
                        
            if int(current_time) % 10 == 0:
                print(f"AI Engine: Heartbeat. Sim Time: {current_time}s. Graph weights updated.")
            
            # Running non-stop for maximum speed (no time.sleep)
            
        except traci.TraCIException as e:
            print(f"AI Engine: Traci connection error: {e}")
            try: traci.close()
            except Exception: pass
            break
        
        except Exception as e:
            print(f"AI Engine: An unexpected error occurred: {e}")
            try: traci.close()
            except Exception: pass
            break

    print("AI Engine: Background thread stopped.")


# --- 7. START THE BACKGROUND THREAD ---
print("AI Engine: Starting background thread...")
ai_thread = threading.Thread(target=update_live_traffic, daemon=True)
ai_thread.start()
time.sleep(3) 

# Helper function to find the closest node ID (placed globally for reuse)
def find_closest_node(target_x, target_y):
    min_dist = float('inf')
    closest_node_id = None
    for node in net.getNodes():
        node_x, node_y = node.getCoord()
        dist = (target_x - node_x)**2 + (target_y - node_y)**2
        if dist < min_dist:
            min_dist = dist
            closest_node_id = node.getID()
    return closest_node_id


# --- 8. DEFINE API ENDPOINTS ---
@app.route('/')
def index():
    return "Pathsync v3 Backend is running!"

@app.route('/route', methods=['POST'])
def get_route():
    try:
        data = request.get_json()
        # ACCEPTS LOCATION NAMES OR COORDINATE STRINGS
        start_name = data['start_name']
        end_name = data['end_name']

        # Helper function to parse coordinates or geocode (shared logic)
        def parse_or_geocode(location_string):
            if ',' in location_string and location_string.count('.') >= 2:
                try:
                    lat, lon = map(float, location_string.split(','))
                    return (lat, lon)
                except ValueError:
                    pass 
            location = geolocator.geocode(f"{location_string}, Mysuru, Karnataka", timeout=10)
            if location:
                return (location.latitude, location.longitude)
            return None

        # --- 1. GEOCORING/PARSING ---
        start_loc_data = parse_or_geocode(start_name)
        end_loc_data = parse_or_geocode(end_name)

        if not start_loc_data or not end_loc_data:
            return jsonify({"status": "error", "message": "Location not recognized or invalid map click."}), 404
        
        start_lat, start_lon = start_loc_data
        end_lat, end_lon = end_loc_data
        
        # --- 2. ROUTING ---
        # Convert GPS coords to SUMO's internal (x, y) coords
        start_x, start_y = net.convertLonLat2XY(start_lon, start_lat)
        end_x, end_y = net.convertLonLat2XY(end_lon, end_lat)

        # Find the nearest graph nodes
        start_node = find_closest_node(start_x, start_y)
        end_node = find_closest_node(end_x, end_y)

        route = nx.shortest_path(G, start_node, end_node, weight='travel_time')
        total_travel_time_seconds = nx.path_weight(G, route, weight='travel_time')
        
        # Get the coordinates (lat/lon) for each node in the route
        route_coords = [[G.nodes[node]['lat'], G.nodes[node]['lon']] for node in route]
        
        return jsonify({
            "status": "success",
            "route_coords": route_coords,
            "total_time_seconds": total_travel_time_seconds
        }), 200

    except nx.NetworkXNoPath:
        return jsonify({"status": "error", "message": "No valid path found. (Check if locations are in the 5km map area)"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/report', methods=['POST'])
def report_incident():
    """
    Accepts an incident location (name or coords) and assigns a high cost to the nearest edge.
    Simulates Incident Detection (Page 3, ERROR413 - Sidharth S.pdf).
    """
    data = request.get_json()
    incident_location_name = data.get('location_name', '')
    incident_type = data.get('type', 'Accident')

    # --- 1. GEOCORING ---
    try:
        location = geolocator.geocode(f"{incident_location_name}, Mysuru, Karnataka", timeout=10)
        
        if not location:
            return jsonify({"status": "error", "message": "Location name not recognized for reporting."}), 404

        lat = location.latitude
        lon = location.longitude
        incident_x, incident_y = net.convertLonLat2XY(lon, lat)

    except Exception:
        return jsonify({"status": "error", "message": "Could not geocode incident location."}), 500

    # --- 2. FIND NEAREST EDGE (Node-to-Edge Search) ---
    try:
        # Find the nearest node (intersection) to the incident location
        nearest_node_id = find_closest_node(incident_x, incident_y)
        
        if not nearest_node_id:
            raise Exception("No nearby nodes found.")

        # Iterate over all outgoing edges from that nearest node
        found_edge_id = None
        for edge in net.getNode(nearest_node_id).getOutgoing():
            found_edge_id = edge.getID()
            break # Take the first outgoing road segment found
        
        if not found_edge_id:
            return jsonify({"status": "error", "message": "No outgoing roads found at the intersection node."}), 404
            
        edge_id = found_edge_id
        u, v = edge_id_to_uv[edge_id] # Get NetworkX nodes

    except Exception:
        # Final safety net error message is improved to be instructional
        return jsonify({"status": "error", "message": "Report location is too far from any mapped road node. Try a more precise street name."}), 404

    # --- 3. APPLY CRITICAL WEIGHT (Incident Detection) ---
    CRITICAL_COST = 999999 

    # FIX: Use G[u][v][key] access syntax for MultiDiGraph
    G[u][v][edge_id]['travel_time'] = CRITICAL_COST
    G[u][v][edge_id]['is_incident'] = True
    
    # Optional: Also apply high cost to the reverse direction if the road is bidirectional
    reverse_edge_id = "-" + edge_id
    if reverse_edge_id in edge_id_to_uv:
        rev_u, rev_v = edge_id_to_uv[reverse_edge_id]
        G[rev_u][rev_v][reverse_edge_id]['travel_time'] = CRITICAL_COST
        G[rev_u][rev_v][reverse_edge_id]['is_incident'] = True

    print(f"INCIDENT DETECTED: {incident_type} reported on edge {edge_id}. Cost set to {CRITICAL_COST}.")

    return jsonify({
        "status": "success",
        "message": f"{incident_type} reported at {incident_location_name}. Routes will now avoid this road."
    }), 200