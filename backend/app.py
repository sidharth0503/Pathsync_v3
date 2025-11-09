import os
import sys
import subprocess
import time
from flask import Flask, jsonify, request
from flask_cors import CORS # Added for frontend communication
from geopy.geocoders import Nominatim # Added for Geocoding (name lookup)
import threading
import networkx as nx
import traci
import sumolib 

# --- 1. CONFIGURE FLASK APP ---
app = Flask(__name__)
CORS(app) # Enable CORS for frontend communication
# Initialize the Nominatim geocoder (uses OpenStreetMap data)
geolocator = Nominatim(user_agent="pathsync-v3-router")
# -----------------------------

# --- 2. FIND AND IMPORT TRACI / SUMOLIB ---
if 'SUMO_HOME' in os.environ:
    tools = os.path.join(os.environ['SUMO_HOME'], 'tools')
    sys.path.append(tools)
else:
    sys.exit("please declare 'SUMO_HOME' as an environment variable")

# --- 3. DEFINE SUMO COMMAND ---
sumoBinary = "sumo"
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
                current_travel_time = traci.edge.getTraveltime(edge_id) # Fix: lowercase 't'
                
                if edge_id in edge_id_to_uv:
                    u, v = edge_id_to_uv[edge_id]
                    G.edges[u, v, edge_id]['travel_time'] = current_travel_time
                        
            if int(current_time) % 10 == 0:
                print(f"AI Engine: Heartbeat. Sim Time: {current_time}s. Graph weights updated.")
            
            time.sleep(1) # Run our loop once per *real* second.
            
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

# --- 8. DEFINE API ENDPOINTS ---
@app.route('/')
def index():
    return "Pathsync v3 Backend is running!"

@app.route('/route', methods=['POST'])
def get_route():
    try:
        data = request.get_json()
        # NOW ACCEPTING LOCATION NAMES
        start_name = data['start_name']
        end_name = data['end_name']

        # --- 1. GEOCORING: Convert Names to Coordinates (Needs Internet) ---
        start_location = geolocator.geocode(f"{start_name}, Mysuru, Karnataka", timeout=10)
        end_location = geolocator.geocode(f"{end_name}, Mysuru, Karnataka", timeout=10)

        if not start_location or not end_location:
            return jsonify({"status": "error", "message": "Location name not recognized or outside the map area."}), 404
        
        start_lat = start_location.latitude
        start_lon = start_location.longitude
        end_lat = end_location.latitude
        end_lon = end_location.longitude
        
        # --- 2. ROUTING: (Original Logic) ---
        # Convert GPS coords to SUMO's internal (x, y) coords
        start_x, start_y = net.convertLonLat2XY(start_lon, start_lat)
        end_x, end_y = net.convertLonLat2XY(end_lon, end_lat)

        # Helper function to find the closest node ID to a given (x, y) point (Robust Search)
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

        # Find the nearest graph nodes
        start_node = find_closest_node(start_x, start_y)
        end_node = find_closest_node(end_x, end_y)

        # Calculate the fastest path using the 'travel_time' weight
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