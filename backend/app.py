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
import atexit

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

# --- 6. LOAD/CREATE YOUR ROUTING GRAPH (FIXED LOGIC) ---
print("Loading SUMO map into NetworkX graph...")

net = sumolib.net.readNet(sumo_net_file)
G = nx.MultiDiGraph() 
edge_id_to_uv = {} # Lookup map for O(n) efficiency

print("Adding routable nodes to graph...")
# --- 1. ADD NODES FIRST ---
# This is the correct logic: We must add nodes with their data *before* adding edges.
# Create a set of all nodes that are part of a 'passenger' (drivable) edge
routable_nodes = set()
for edge in net.getEdges():
    if edge.allows("passenger"):
        routable_nodes.add(edge.getFromNode())
        routable_nodes.add(edge.getToNode())

for node in routable_nodes:
    node_id = node.getID()
    x, y = node.getCoord()
    lon, lat = net.convertXY2LonLat(x, y)
    # This ensures every node in G has its x, y, lon, lat attributes
    G.add_node(node_id, x=x, y=y, lon=lon, lat=lat)

print(f"Added {len(G.nodes())} routable nodes.")

# --- 2. NOW, ADD EDGES ---
print("Adding routable edges to graph...")
routable_edge_count = 0
for edge in net.getEdges():
    if not edge.allows("passenger"):
        continue

    edge_id = edge.getID()
    from_node = edge.getFromNode().getID()
    to_node = edge.getToNode().getID()
    
    # We only add edges that connect nodes we've already added
    if from_node in G and to_node in G:
        length = edge.getLength()
        speed = edge.getSpeed()
        travel_time = length / speed
        
        G.add_edge(from_node, to_node, key=edge_id, length=length, travel_time=travel_time)
        edge_id_to_uv[edge_id] = (from_node, to_node)
        routable_edge_count += 1
    
print(f"NetworkX graph 'G' created successfully with {routable_edge_count} routable edges.")


# --- 5. DEFINE "AI ENGINE" HEARTBEAT FUNCTION (FIXED FOR SPEED AND RUNTIME) ---
# --- 5. DEFINE "AI ENGINE" HEARTBEAT FUNCTION (FIXED FOR SPEED AND RUNTIME) ---
# --- 5. DEFINE "AI ENGINE" HEARTBEAT FUNCTION (FINAL VERSION) ---
# --- 5. DEFINE "AI ENGINE" HEARTBEAT FUNCTION (FINAL VERSION) ---
def update_live_traffic():
    """
    (FINAL) The main "AI Engine" loop.
    - Manually breaks the loop after 3600s.
    - Implements AI Smart Traffic Light control (with correct phase logic).
    - Updates graph weights for routing.
    """
    
    print("AI Engine: Background thread started. Attempting to launch and connect...")
    
    try:
        traci.start(sumoCmd, port=8813)
        print("AI Engine: SUMO started and Traci connected successfully.")
        
        # --- GET ALL TRAFFIC LIGHT IDs ---
        traffic_light_ids = traci.trafficlight.getIDList()
        print(f"AI Engine: Found {len(traffic_light_ids)} traffic lights to manage.")
        
    except Exception as e:
        print(f"AI Engine: Failed to launch/connect to Traci: {e}")
        return

    # --- This is the main loop ---
    try:
        while True:
            traci.simulationStep()
            current_time = traci.simulation.getTime()

            # --- 1. RUNTIME FIX ---
            if current_time > 3600:
                print(f"AI Engine: Simulation time {current_time}s > 3600s. Stopping simulation.")
                break # Exit the 'while True' loop
            
            # --- 2. AI & ROUTING LOGIC (Runs every 10s for speed) ---
            if int(current_time) % 10 == 0:
            
                # --- NEW: AI SMART TRAFFIC LIGHT LOGIC (CORRECTED) ---
                for tl_id in traffic_light_ids:
                    # Get all incoming lanes controlled by this light
                    incoming_lanes = set(traci.trafficlight.getControlledLanes(tl_id))
                    if not incoming_lanes:
                        continue # Skip if this light controls no lanes

                    # Find the lane with the most waiting cars
                    best_lane = max(incoming_lanes, key=traci.lane.getLastStepHaltingNumber)
                    
                    if traci.lane.getLastStepHaltingNumber(best_lane) == 0:
                        continue # No cars are waiting, skip this light
                    
                    # --- THIS IS THE CRITICAL FIX ---
                    # Get the full 'program' for the light, which contains *only settable green phases*
                    logic = traci.trafficlight.getCompleteRedYellowGreenDefinition(tl_id)
                    if not logic:
                        continue

                    # logic[0].phases contains the list of *settable* Phase objects
                    settable_phases = logic[0].phases
                    controlled_lanes = traci.trafficlight.getControlledLanes(tl_id)
                    best_phase_index = -1 # This will be the index *of the settable phase* (e.g., 0, 1, 2, 3)

                    # Find which of the "green" phases serves our 'best_lane'
                    for i, phase in enumerate(settable_phases):
                        try:
                            # Find where our 'best_lane' is in the state string
                            lane_index_in_state = controlled_lanes.index(best_lane)
                            
                            # Check the state string (e.g., "GrGrGrrr") at that lane's index
                            if lane_index_in_state < len(phase.state):
                                lane_state = phase.state[lane_index_in_state].lower()
                                if lane_state == 'g': # 'g' or 'G' means green
                                    best_phase_index = i # 'i' is the *correct* settable phase index
                                    break
                        except Exception:
                            # This can fail if 'best_lane' isn't in 'controlled_lanes' for some reason
                            pass 
                    
                    # If we found a valid green phase and it's not already active, set it.
                    current_phase = traci.trafficlight.getPhase(tl_id)
                    if best_phase_index != -1 and current_phase != best_phase_index:
                        traci.trafficlight.setPhase(tl_id, best_phase_index)
                
                # --- OLD: DIGITAL TWIN (Graph Weight Update) ---
                all_sumo_edges = traci.edge.getIDList()
                for edge_id in all_sumo_edges:
                    if edge_id in edge_id_to_uv:
                        u, v = edge_id_to_uv[edge_id]
                        if G.edges[u, v, edge_id].get('is_incident') != True:
                            current_travel_time = traci.edge.getTraveltime(edge_id) 
                            G.edges[u, v, edge_id]['travel_time'] = current_travel_time
                            
                print(f"AI Engine: Heartbeat. Sim Time: {current_time}s. Lights updated. Graph weights updated.")

    except traci.TraCIException as e:
        print(f"AI Engine: Traci connection error (simulation likely ended): {e}")
    except Exception as e:
        print(f"AI Engine: An unexpected error occurred: {e}")
    finally:
        print("AI Engine: Background thread stopping. Closing Traci connection.")
        try:
            traci.close()
        except Exception:
            pass 

    print("AI Engine: Background thread stopped.")


# --- 7. START THE BACKGROUND THREAD ---
print("AI Engine: Starting background thread...")
ai_thread = threading.Thread(target=update_live_traffic, daemon=True)
ai_thread.start()
time.sleep(3) # Give SUMO time to boot

# --- 7. START THE BACKGROUND THREAD ---
print("AI Engine: Starting background thread...")
ai_thread = threading.Thread(target=update_live_traffic, daemon=True)
ai_thread.start()
time.sleep(3) # Give SUMO time to boot

# --- NEW: Ensure SUMO closes when Flask stops ---
@atexit.register
def cleanup_sumo():
    """
    This function is automatically called when the Flask app is stopped.
    """
    print("Flask server shutting down... closing Traci and SUMO.")
    try:
        traci.close()
    except Exception as e:
        print(f"Error closing Traci: {e}")
# -----------------------------------------------

# Helper function to find the closest node ID...
# (the rest of your code)

# Helper function to find the closest node ID (FIXED and SAFER)
def find_closest_node(target_x, target_y):
    min_dist = float('inf')
    closest_node_id = None
    
    # Safety check in case graph is empty
    if not G.nodes():
        return None
        
    # Iterate nodes more efficiently and safely
    for node_id, node_data in G.nodes(data=True):
        # This will now work, because G.add_node() guarantees 'x' and 'y' exist
        dist = (target_x - node_data['x'])**2 + (target_y - node_data['y'])**2
        if dist < min_dist:
            min_dist = dist
            closest_node_id = node_id
    return closest_node_id

# --- GLOBAL HELPER FUNCTION ---
def parse_or_geocode(location_string):
    """
    Parses a "lat,lon" string or geocodes a location name.
    Returns (lat, lon) or None.
    """
    if ',' in location_string and location_string.count('.') >= 2:
        try:
            lat, lon = map(float, location_string.split(','))
            return (lat, lon)
        except ValueError:
            pass  
            
    try:
        location = geolocator.geocode(f"{location_string}, Mysuru, Karnataka", timeout=10)
        if location:
            return (location.latitude, location.longitude)
    except Exception:
        return None 
        
    return None

# --- 8. DEFINE API ENDPOINTS ---
@app.route('/')
def index():
    return "Pathsync v3 Backend is running!"

@app.route('/route', methods=['POST'])
def get_route():
    try:
        data = request.get_json()
        start_name = data['start_name']
        end_name = data['end_name']

        start_loc_data = parse_or_geocode(start_name)
        end_loc_data = parse_or_geocode(end_name)

        if not start_loc_data or not end_loc_data:
            return jsonify({"status": "error", "message": "Location not recognized or invalid map click."}), 404
        
        start_lat, start_lon = start_loc_data
        end_lat, end_lon = end_loc_data
        
        start_x, start_y = net.convertLonLat2XY(start_lon, start_lat)
        end_x, end_y = net.convertLonLat2XY(end_lon, end_lat)

        start_node = find_closest_node(start_x, start_y)
        end_node = find_closest_node(end_x, end_y)
        
        if not start_node or not end_node:
             return jsonify({"status": "error", "message": "Could not find a routable road near one of the locations."}), 404

        route = nx.shortest_path(G, start_node, end_node, weight='travel_time')
        total_travel_time_seconds = nx.path_weight(G, route, weight='travel_time')
        
        route_coords = [[G.nodes[node]['lat'], G.nodes[node]['lon']] for node in route]
        
        return jsonify({
            "status": "success",
            "route_coords": route_coords,
            "total_time_seconds": total_travel_time_seconds
        }), 200

    except nx.NetworkXNoPath:
        return jsonify({"status": "error", "message": "No valid path found. (The roads may be disconnected or blocked by an incident)"}), 404
    except Exception as e:
        print(f"--- CRITICAL ERROR in /route --- \n{e}\n--- END ERROR ---")
        return jsonify({"status": "error", "message": "An error occurred on the server during routing."}), 500


@app.route('/report', methods=['POST'])
def report_incident():
    """
    (FINAL FIX) Accepts an incident location, finds the nearest drivable edge, 
    and blocks BOTH directions of that edge.
    """
    data = request.get_json()
    incident_location_name = data.get('location_name', '')
    incident_type = data.get('type', 'Accident')

    # --- 1. GEOCORING/PARSING (This part is correct) ---
    try:
        loc_data = parse_or_geocode(incident_location_name)
        if not loc_data:
            return jsonify({"status": "error", "message": "Location name or coordinate not recognized."}), 404
        lat, lon = loc_data
        incident_x, incident_y = net.convertLonLat2XY(lon, lat)
    except Exception as e:
        return jsonify({"status": "error", "message": f"Could not geocode/parse location: {e}"}), 500

    # --- 2. FIND NEAREST EDGE (NOW SAFER AND CORRECTED) ---
    try:
        # Get a list of (edge, distance) tuples, sorted by distance
        nearest_edge_list = net.getNeighboringEdges(incident_x, incident_y, r=200) 
        if not nearest_edge_list:
             return jsonify({"status": "error", "message": "Report location is too far from any mapped road."}), 404
        
        nearest_edge_list.sort(key=lambda x: x[1])

        # Find the *first* edge in the list that is a drivable 'passenger' road
        edge_to_block = None
        for edge, dist in nearest_edge_list:
            if edge.getID() in edge_id_to_uv:
                edge_to_block = edge # This is the sumolib.net.Edge object
                break 
        
        if not edge_to_block:
            return jsonify({"status": "error", "message": "Reported location is on a non-drivable road. Try clicking the middle of a main street."}), 404
            
        # --- 3. APPLY CRITICAL WEIGHT (THE FINAL FIX) ---
        CRITICAL_COST = 999999 
        edges_blocked = 0

        # --- Block the edge itself ---
        edge_id = edge_to_block.getID()
        u, v = edge_id_to_uv[edge_id]
        G[u][v][edge_id]['travel_time'] = CRITICAL_COST
        G[u][v][edge_id]['is_incident'] = True
        edges_blocked += 1
        
        print(f"INCIDENT: Blocked primary edge {edge_id}")

        # --- Block the INVERSE edge ---
        inverse_edge = edge_to_block.getInverse()
        if inverse_edge and inverse_edge.getID() in edge_id_to_uv:
            inv_edge_id = inverse_edge.getID()
            inv_u, inv_v = edge_id_to_uv[inv_edge_id]
            
            G[inv_u][inv_v][inv_edge_id]['travel_time'] = CRITICAL_COST
            G[inv_u][inv_v][inv_edge_id]['is_incident'] = True
            edges_blocked += 1
            print(f"INCIDENT: Blocked inverse edge {inv_edge_id}")

        print(f"INCIDENT DETECTED: {incident_type} reported. {edges_blocked} edge(s) blocked.")

        return jsonify({
            "status": "success",
            "message": f"{incident_type} reported successfully. Routes will now avoid this road."
        }), 200

    except Exception as e:
        print(f"--- CRITICAL ERROR in /report ---")
        print(f"Exception: {e}")
        print(f"--- END ERROR ---")
        return jsonify({"status": "error", "message": "An unexpected server error occurred."}), 500