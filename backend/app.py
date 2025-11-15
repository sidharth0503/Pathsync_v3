import os
import sys
import subprocess
import time
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS 
from geopy.geocoders import Nominatim 
import threading # <-- BUGFIX: IMPORT THREADING
import networkx as nx
import traci
import sumolib 
import atexit
import logging 
from collections import deque 

# --- 1. CONFIGURE FLASK APP ---
app = Flask(__name__)
CORS(app) 
geolocator = Nominatim(user_agent="pathsync-v3-router")
g_traci_latency_ms = 0.0

# --- FIX: Prevent Flask Reloader from running twice ---
sim_thread_started = False
# ---------------------------------------------------

# --- GLOBAL STATE FOR ADMIN DASHBOARD ---
ADMIN_STATE = {
    "traffic_lights": {}, 
    "total_routes_calculated": 0, 
    "total_incidents_reported": 0 
}
# --- INCIDENT HISTORY ---
INCIDENT_HISTORY = []
history_lock = threading.Lock()
# --- RESOLVED INCIDENT HISTORY ---
RESOLVED_INCIDENT_HISTORY = []
resolved_history_lock = threading.Lock()
# --- TRAFFIC LIGHT LOCATIONS ---
g_traffic_light_locations = {}
# -----------------------------

# --- BUGFIX: ADD GLOBAL LOCK FOR GRAPH 'G' ---
g_lock = threading.Lock()
# ---------------------------------------------


# --- THREAD-SAFE LOGGING SETUP ---
log_queue = deque(maxlen=50) 
log_lock = threading.Lock()

class AdminLogHandler(logging.Handler):
    """A custom log handler that captures logs for the admin panel."""
    def emit(self, record):
        msg = self.format(record)
        with log_lock:
            log_queue.append(msg)

# Create and configure the handler
admin_handler = AdminLogHandler()
admin_handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(message)s', '%H:%M:%S')
admin_handler.setFormatter(formatter)

# Get the root logger (to capture logging.info)
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
# Remove all existing handlers to avoid duplicates
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)
root_logger.addHandler(admin_handler)

# Also add to Flask's specific logger
app.logger.addHandler(admin_handler)
# ----------------------------------------


# --- 2. FIND AND IMPORT TRACI / SUMOLIB ---
if 'SUMO_HOME' in os.environ:
    tools = os.path.join(os.environ['SUMO_HOME'], 'tools')
    sys.path.append(tools)
else:
    sys.exit("please declare 'SUMO_HOME' as an environment variable")

# --- 3. DEFINE SUMO COMMAND ---
sumoBinary = "sumo" # <-- Kept as sumo-gui for debugging
sumo_net_file = "simulation/map.net.xml" 
sumoConfig = "simulation/map.sumocfg"

sumoCmd = [
    sumoBinary,
    "-c", sumoConfig,
    "--gui-settings-file", "simulation/map.view.xml",
    "--start",  # --- ADDED AUTO-START ---
    "--end", "3600" 
]

# --- 6. LOAD/CREATE YOUR ROUTING GRAPH (FIXED "ISLAND" BUG) ---
app.logger.info("Loading SUMO map into NetworkX graph...")

net = sumolib.net.readNet(sumo_net_file)
G = nx.MultiDiGraph() 
edge_id_to_uv = {} # Lookup map for O(n) efficiency

app.logger.info("Adding ALL nodes to graph...")
for node in net.getNodes():
    node_id = node.getID()
    x, y = node.getCoord()
    lon, lat = net.convertXY2LonLat(x, y)
    G.add_node(node_id, x=x, y=y, lon=lon, lat=lat)
app.logger.info(f"Added {len(G.nodes())} total nodes.")

# --- 2. NOW, ADD ALL EDGES ---
app.logger.info("Adding ALL edges to graph...")
routable_edge_count = 0
for edge in net.getEdges():
    edge_id = edge.getID()
    from_node = edge.getFromNode().getID()
    to_node = edge.getToNode().getID()
    
    if from_node in G and to_node in G:
        length = edge.getLength()
        speed = edge.getSpeed()
        original_travel_time = length / (speed + 0.001) # Store original
        
        G.add_edge(
            from_node, to_node, key=edge_id, 
            length=length, 
            travel_time=original_travel_time,
            original_travel_time=original_travel_time
        )
        edge_id_to_uv[edge_id] = (from_node, to_node)
        routable_edge_count += 1
    
app.logger.info(f"NetworkX graph 'G' created successfully with {routable_edge_count} routable edges.")
# --- END OF GRAPH LOADING FIX ---


# --- 5. DEFINE "AI ENGINE" HEARTBEAT FUNCTION (FINAL - "DUAL AI") ---
# --- 5. DEFINE "AI ENGINE" HEARTBEAT FUNCTION (FINAL - "DUAL AI") ---
def update_live_traffic():
    """
    (FINAL - ADMIN READY) The main "AI Engine" loop.
    Populates ADMIN_STATE for the dashboard and is fully thread-safe.
    """
    global g_traci_latency_ms
    global g_traffic_light_locations
    global sim_thread_started # <-- FIX
    sim_thread_started = True # <-- FIX
    
    app.logger.info("AI Engine: Background thread started. Attempting to launch and connect...")
    
    try:
        traci.start(sumoCmd, port=8813)
        app.logger.info("AI Engine: SUMO started and Traci connected successfully.")
        
        # --- AI INCIDENT DETECTOR STATE ---
        jam_tracker = {} # Stores how long an edge has been "jammed"
        JAM_HALT_THRESHOLD = 5 # 10 stopped cars
        JAM_TIME_THRESHOLD = 10 # 30 seconds
        
        # --- FIX: Moved location finding to *inside* the loop ---
        traffic_lights_initialized = False

    except Exception as e:
        app.logger.error(f"AI Engine: Failed to launch/connect to Traci: {e}")
        sim_thread_started = False # <-- FIX: Allow re-run
        return # <-- This stops the thread if Traci fails to start

    # --- This is the main loop ---
    try:
        while True:
            traci.simulationStep() # <-- This will now be called
            
            # --- LATENCY MEASUREMENT ---
            start_t = time.time()
            current_time = traci.simulation.getTime() # This is our "ping"
            end_t = time.time()
            current_latency_ms = (end_t - start_t) * 1000
            if g_traci_latency_ms == 0.0:
                g_traci_latency_ms = current_latency_ms
            else:
                g_traci_latency_ms = (0.1 * current_latency_ms) + (0.9 * g_traci_latency_ms)
            # --- END LATENCY ---
            
            # --- FINAL FIX: One-time initialization for traffic light locations ---
            if not traffic_lights_initialized:
                app.logger.info("Fetching all traffic light locations...")
                
                # 1. Get all *known* junction IDs
                all_junction_ids = set(traci.junction.getIDList())
                
                # 2. Get all traffic light IDs
                all_traffic_light_ids = traci.trafficlight.getIDList()
                app.logger.info(f"Found {len(all_traffic_light_ids)} total traffic lights in network file.")
                
                for tl_id in all_traffic_light_ids:
                    try:
                        # 3. Check if the traffic light ID is also a known junction ID
                        if tl_id in all_junction_ids:
                            x, y = traci.junction.getPosition(tl_id)
                            lon, lat = net.convertXY2LonLat(x, y)
                            g_traffic_light_locations[tl_id] = (lat, lon)
                        else:
                            # 4. If not, it's a complex light. Skip it.
                            app.logger.warning(f"Traffic light '{tl_id}' is not a simple junction. Skipping.")
                            
                    except Exception as e:
                        # 5. Catch any other Traci errors
                        app.logger.warning(f"Error getting location for traffic light '{tl_id}': {e}. Skipping.")
                        
                app.logger.info(f"Located {len(g_traffic_light_locations)} traffic lights.")
                traffic_lights_initialized = True
            # --- END FIX ---


            if current_time > 3600:
                app.logger.info(f"AI Engine: Simulation time {current_time}s > 3600s. Stopping simulation.")
                break 
            
            # --- Update traffic light states (every 2 seconds) ---
            if int(current_time) % 2 == 0:
                signal_states = []
                for tl_id, (lat, lon) in g_traffic_light_locations.items():
                    try:
                        state_string = traci.trafficlight.getRedYellowGreenState(tl_id)
                        
                        # --- START OF TRAFFIC LIGHT LOGIC FIX ---
                        # Get the state of the very first light in the string
                        first_light_state = state_string.lower()[0] 
                        
                        if first_light_state == 'g':
                            simple_state = 'green'
                        elif first_light_state == 'y':
                            simple_state = 'yellow'
                        else: # Covers 'r' (red) or other unknown states
                            simple_state = 'red'
                        # --- END OF TRAFFIC LIGHT LOGIC FIX ---
                            
                        signal_states.append({
                            "id": tl_id,
                            "lat": lat,
                            "lon": lon,
                            "state": simple_state
                        })
                    except Exception:
                        pass # Ignore if a signal disappears
                ADMIN_STATE["traffic_light_states"] = signal_states
            
            
            # --- AI & ROUTING LOGIC (Runs every 10s for speed) ---
            if int(current_time) % 10 == 0:
            
                # --- BUGFIX: LOCK THE GRAPH 'G' FOR ALL READS/WRITES ---
                with g_lock:
                    # --- DIGITAL TWIN (Graph Weight Update) ---
                    edge_heatmap_data = [] # For the heatmap

                    all_sumo_edges = traci.edge.getIDList()
                    for edge_id in all_sumo_edges:
                        if edge_id in edge_id_to_uv:
                            u, v = edge_id_to_uv[edge_id]

                            # 1. Check if graph 'G' says this edge is an incident
                            if G.edges[u, v, edge_id].get('is_incident') == True:
                                traci.edge.setMaxSpeed(edge_id, 0.1)
                            
                            # 2. If 'G' says it's NOT an incident
                            else:
                                original_edge = net.getEdge(edge_id)
                                traci.edge.setMaxSpeed(edge_id, original_edge.getSpeed())

                                # 2b. Run AI 2: AUTOMATIC INCIDENT DETECTOR
                                is_normal_edge = not edge_id.startswith(":") and "#" not in edge_id
                                if is_normal_edge:
                                    halting_cars = traci.edge.getLastStepHaltingNumber(edge_id)
                                    
                                    if halting_cars > JAM_HALT_THRESHOLD:
                                        jam_tracker[edge_id] = jam_tracker.get(edge_id, 0) + 10 
                                        if jam_tracker[edge_id] >= JAM_TIME_THRESHOLD:
                                            app.logger.info(f"--- AUTO-INCIDENT: Halting queue detected on edge {edge_id}! Applying CRITICAL_COST. ---")
                                            G[u][v][edge_id]['travel_time'] = 999999
                                            G[u][v][edge_id]['is_incident'] = True
                                            
                                            # --- SAFE INVERSE EDGE BLOCK ---
                                            inverse_edge_id = ""
                                            if edge_id.startswith("-"):
                                                inverse_edge_id = edge_id[1:]
                                            else:
                                                inverse_edge_id = "-" + edge_id
                                            
                                            if inverse_edge_id in edge_id_to_uv:
                                                inv_u, inv_v = edge_id_to_uv[inverse_edge_id]
                                                G[inv_u][inv_v][inverse_edge_id]['travel_time'] = 999999
                                                G[inv_u][inv_v][inverse_edge_id]['is_incident'] = True
                                                app.logger.info(f"--- AUTO-INCIDENT: Also applied CRITICAL_COST to inverse edge {inverse_edge_id}. ---")
                                                
                                    else:
                                        jam_tracker[edge_id] = 0
                                
                                # 2c. Update travel time from SUMO
                                if G.edges[u, v, edge_id].get('is_incident') != True:
                                    current_travel_time = traci.edge.getTraveltime(edge_id)
                                    G.edges[u, v, edge_id]['travel_time'] = current_travel_time

                                    # Add data for heatmap
                                    original_time = G.edges[u, v, edge_id]['original_travel_time']
                                    intensity = current_travel_time / (original_time + 0.001)
                                    node_data = G.nodes[u]
                                    edge_heatmap_data.append({
                                        "lat": node_data['lat'],
                                        "lon": node_data['lon'],
                                        "intensity": intensity
                                    })

                    ADMIN_STATE["edge_heatmap_data"] = edge_heatmap_data
                # --- END OF g_lock ---
                
                app.logger.info(f"AI Engine: Heartbeat. Sim Time: {current_time}s.")

    except traci.TraCIException as e:
        app.logger.warning(f"AI Engine: Traci connection error (simulation likely ended): {e}")
    except Exception as e:
        app.logger.error(f"AI Engine: An unexpected error occurred: {e}")
    finally:
        app.logger.info("AI Engine: Background thread stopping. Closing Traci connection.")
        try:
            traci.close()
        except Exception:
            pass 

    app.logger.info("AI Engine: Background thread stopped.")


# --- 7. START THE BACKGROUND THREAD ---
app.logger.info("AI Engine: Starting background thread...")
# --- FIX: Check flag before starting thread ---
if not sim_thread_started:
    ai_thread = threading.Thread(target=update_live_traffic, daemon=True)
    ai_thread.start()
else:
    app.logger.info("AI Engine: Thread already started by reloader.")
# --- END FIX ---
time.sleep(3) # Give SUMO time to boot

# --- ENSURE SUMO CLOSES ---
@atexit.register
def cleanup_sumo():
    app.logger.info("Flask server shutting down... closing Traci and SUMO.")
    try:
        traci.close()
    except Exception as e:
        app.logger.error(f"Error closing Traci: {e}")
# -----------------------------------------------

# Helper function to find the closest node ID
def find_closest_node(target_x, target_y):
    min_dist = float('inf')
    closest_node_id = None
    if not G.nodes():
        return None
    for node_id, node_data in G.nodes(data=True):
        dist = (target_x - node_data['x'])**2 + (target_y - node_data['y'])**2
        if dist < min_dist:
            min_dist = dist
            closest_node_id = node_id
    return closest_node_id

# --- GLOBAL HELPER FUNCTION ---
def parse_or_geocode(location_string):
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
        
        # --- BUGFIX: LOCK THE GRAPH 'G' FOR READING ---
        with g_lock:
            route = nx.shortest_path(G, start_node, end_node, weight='travel_time')
            total_travel_time_seconds = nx.path_weight(G, route, weight='travel_time')
            total_distance_meters = nx.path_weight(G, route, weight='length')
            route_coords = [[G.nodes[node]['lat'], G.nodes[node]['lon']] for node in route]
        # --- END OF g_lock ---
            
        return jsonify({
            "status": "success",
            "route_coords": route_coords,
            "total_time_seconds": total_travel_time_seconds,
            "total_distance_meters": total_distance_meters
        }), 200
    except nx.NetworkXNoPath:
        return jsonify({"status": "error", "message": "No valid path found. (The roads may be disconnected or blocked by an incident)"}), 404
    except Exception as e:
        app.logger.error(f"--- CRITICAL ERROR in /route --- \n{e}\n--- END ERROR ---")
        return jsonify({"status": "error", "message": "An error occurred on the server during routing."}), 500


@app.route('/report', methods=['POST'])
def report_incident():
    data = request.get_json()
    incident_location_name = data.get('location_name', '')
    incident_type = data.get('type', 'Accident')
    try:
        loc_data = parse_or_geocode(incident_location_name)
        if not loc_data:
            return jsonify({"status": "error", "message": "Location name or coordinate not recognized."}), 404
        lat, lon = loc_data # <-- This is the *exact* clicked location
        incident_x, incident_y = net.convertLonLat2XY(lon, lat)
    except Exception as e:
        return jsonify({"status": "error", "message": f"Could not geocode/parse location: {e}"}), 500
    try:
        nearest_edge_list = net.getNeighboringEdges(incident_x, incident_y, r=200) 
        if not nearest_edge_list:
             return jsonify({"status": "error", "message": "Report location is too far from any mapped road."}), 404
        nearest_edge_list.sort(key=lambda x: x[1])
        edge_to_block = None
        for edge, dist in nearest_edge_list:
            if edge.getID() in edge_id_to_uv:
                edge_to_block = edge 
                break 
        if not edge_to_block:
            return jsonify({"status": "error", "message": "Reported location is on a non-drivable road. Try clicking the middle of a main street."}), 404
        
        with history_lock:
            INCIDENT_HISTORY.append(time.time())
            
        CRITICAL_COST = 999999 
        edges_blocked = 0
        edge_id = edge_to_block.getID()
        
        # --- BUGFIX: LOCK THE GRAPH 'G' FOR WRITING ---
        with g_lock:
            if edge_id in edge_id_to_uv:
                u, v = edge_id_to_uv[edge_id]
                G[u][v][edge_id]['travel_time'] = CRITICAL_COST
                G[u][v][edge_id]['is_incident'] = True
                
                # --- FIX: Store the *exact* clicked lat/lon ---
                G[u][v][edge_id]['incident_lat'] = lat
                G[u][v][edge_id]['incident_lon'] = lon
                # --- END FIX ---
                
                edges_blocked += 1
                app.logger.info(f"INCIDENT (Manual): Flagged primary edge {edge_id}")
        # --- END OF g_lock ---
        
        app.logger.info(f"INCIDENT DETECTED: {incident_type} reported. {edges_blocked} edge(s) flagged.")
        
        return jsonify({
            "status": "success",
            "message": f"{incident_type} reported successfully. Routes will now avoid this road."
        }), 200

    except Exception as e:
        app.logger.error(f"--- CRITICAL ERROR in /report ---")
        app.logger.error(f"Exception: {e}")
        app.logger.error(f"--- END ERROR ---")
        return jsonify({"status": "error", "message": "An unexpected server error occurred."}), 500


# ==========================================================
# --- ADMIN API ENDPOINTS ---
# ==========================================================

@app.route('/admin/dashboard_data')
def get_dashboard_data():
    """
    Provides all live data for the admin dashboard.
    """
    # 1. Get all active incidents from the graph
    incidents = []
    try:
        # --- BUGFIX: LOCK THE GRAPH 'G' FOR READING ---
        with g_lock:
            for u, v, key, data in G.edges(keys=True, data=True):
                if data.get('is_incident') == True:
                    node_data = G.nodes[u] # Get node data as a fallback
                    incidents.append({
                        "edge_id": key,
                        # --- FIX: Use specific incident lat/lon if it exists ---
                        "lat": data.get('incident_lat', node_data['lat']),
                        "lon": data.get('incident_lon', node_data['lon'])
                    })
        # --- END OF g_lock ---
            
    except Exception as e:
        app.logger.error(f"Error reading graph incidents: {e}")

    # 2. Get heatmap data
    heatmap_data = ADMIN_STATE.get("edge_heatmap_data", [])
    
    # 3. Get traffic light data
    traffic_light_states = ADMIN_STATE.get("traffic_light_states", [])

    return jsonify({
        "stats": {
            "total_incidents_reported": len(INCIDENT_HISTORY)
        },
        "incidents": incidents,
        "edge_heatmap_data": heatmap_data,
        "traffic_light_states": traffic_light_states
    })

@app.route('/admin/get_logs')
def get_logs():
    with log_lock:
        logs = list(log_queue)
    return jsonify({"logs": logs})

@app.route('/admin/incident_history')
def get_incident_history():
    with history_lock:
        history = list(INCIDENT_HISTORY)
    return jsonify({"history": history})

@app.route('/admin/resolved_history')
def get_resolved_history():
    with resolved_history_lock:
        history = list(RESOLVED_INCIDENT_HISTORY)
    return jsonify({"history": history})


@app.route('/admin/unblock_edge', methods=['POST'])
def unblock_edge():
    data = request.get_json()
    edge_id = data.get('edge_id')
    if not edge_id or edge_id not in edge_id_to_uv:
        return jsonify({"status": "error", "message": "Invalid edge_id"}), 404
    try:
        # --- BUGFIX: LOCK THE GRAPH 'G' FOR WRITING ---
        with g_lock:
            u, v = edge_id_to_uv[edge_id]
            if G.edges[u, v, edge_id].get('is_incident') == True:
                with resolved_history_lock:
                    RESOLVED_INCIDENT_HISTORY.append(time.time())
                app.logger.info(f"--- ADMIN: Manually flagged edge {edge_id} for unblocking ---")
            
            # --- FIX: Clear all incident data from the edge ---
            G.edges[u, v, edge_id]['is_incident'] = False
            G.edges[u, v, edge_id].pop('incident_lat', None)
            G.edges[u, v, edge_id].pop('incident_lon', None)
            # --- END FIX ---
            
            edge = net.getEdge(edge_id)
            travel_time = edge.getLength() / (edge.getSpeed() + 0.001)
            G.edges[u, v, edge_id]['travel_time'] = travel_time
            
            inverse_edge_id = ""
            if edge_id.startswith("-"):
                inverse_edge_id = edge_id[1:]
            else:
                inverse_edge_id = "-" + edge_id
            
            if inverse_edge_id in edge_id_to_uv:
                inv_u, inv_v = edge_id_to_uv[inverse_edge_id]
                
                # --- FIX: Clear all incident data from inverse edge ---
                G.edges[inv_u, inv_v, inverse_edge_id]['is_incident'] = False
                G.edges[inv_u, inv_v, inverse_edge_id].pop('incident_lat', None)
                G.edges[inv_u, inv_v, inverse_edge_id].pop('incident_lon', None)
                # --- END FIX ---

                inv_edge = net.getEdge(inverse_edge_id)
                inv_travel_time = inv_edge.getLength() / (inv_edge.getSpeed() + 0.001)
                G.edges[inv_u, inv_v, inverse_edge_id]['travel_time'] = inv_travel_time
        # --- END OF g_lock ---
        
        return jsonify({"status": "success", "message": f"Edge {edge_id} flagged for unblocking."})
    except Exception as e:
        app.logger.error(f"Error unblocking edge: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/status')
def get_status():
    global g_traci_latency_ms
    return jsonify({
        "status": "success",
        "traci_latency_ms": g_traci_latency_ms
    }), 200

# ==========================================================
# --- ROUTES TO SERVE THE ADMIN WEB PAGE ---
# ==========================================================

@app.route('/admin')
def serve_admin_page():
    return send_from_directory(os.path.join(os.path.dirname(__file__), '..', 'admin'), 'admin.html')

@app.route('/admin/<path:filename>')
def serve_admin_static(filename):
    return send_from_directory(os.path.join(os.path.dirname(__file__), '..', 'admin'), filename)

# ==========================================================
    
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)