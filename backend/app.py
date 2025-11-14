import os
import sys
import subprocess
import time
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS 
from geopy.geocoders import Nominatim 
import threading
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
g_traci_latency_ms = 0.0 # <-- NEW: Global for latency

# --- GLOBAL STATE FOR ADMIN DASHBOARD ---
ADMIN_STATE = {
    "traffic_lights": {}, 
    "total_routes_calculated": 0, 
    "total_incidents_reported": 0 
}
# --- NEW: INCIDENT HISTORY ---
INCIDENT_HISTORY = []
history_lock = threading.Lock()
# -----------------------------

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
sumoBinary = "sumo-gui" # <-- Kept as sumo-gui for debugging
sumo_net_file = "simulation/map.net.xml" 
sumoConfig = "simulation/map.sumocfg"

sumoCmd = [
    sumoBinary,
    "-c", sumoConfig,
    "--gui-settings-file", "simulation/map.view.xml",
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
            original_travel_time=original_travel_time # <-- NEW
        )
        edge_id_to_uv[edge_id] = (from_node, to_node)
        routable_edge_count += 1
    
app.logger.info(f"NetworkX graph 'G' created successfully with {routable_edge_count} routable edges.")
# --- END OF GRAPH LOADING FIX ---


# --- 5. DEFINE "AI ENGINE" HEARTBEAT FUNCTION (FINAL - "DUAL AI") ---
def update_live_traffic():
    """
    (FINAL - ADMIN READY) The main "AI Engine" loop.
    Populates ADMIN_STATE for the dashboard and is fully thread-safe.
    """
    global g_traci_latency_ms # <-- NEW: Get global latency var
    
    app.logger.info("AI Engine: Background thread started. Attempting to launch and connect...")
    
    try:
        traci.start(sumoCmd, port=8813)
        app.logger.info("AI Engine: SUMO started and Traci connected successfully.")
        
        # --- REMOVED TRAFFIC LIGHT LOGIC ---
        
        # --- AI INCIDENT DETECTOR STATE ---
        jam_tracker = {} # Stores how long an edge has been "jammed"
        JAM_HALT_THRESHOLD = 10 # 10 stopped cars
        JAM_TIME_THRESHOLD = 30 # 30 seconds
        
    except Exception as e:
        app.logger.error(f"AI Engine: Failed to launch/connect to Traci: {e}")
        return

    # --- This is the main loop ---
    try:
        while True:
            traci.simulationStep()
            
            # --- NEW: LATENCY MEASUREMENT ---
            start_t = time.time()
            current_time = traci.simulation.getTime() # This is our "ping"
            end_t = time.time()
            
            current_latency_ms = (end_t - start_t) * 1000
            
            # Apply smoothing (Exponential Moving Average)
            if g_traci_latency_ms == 0.0:
                g_traci_latency_ms = current_latency_ms # Initialize
            else:
                g_traci_latency_ms = (0.1 * current_latency_ms) + (0.9 * g_traci_latency_ms)
            # --- END OF LATENCY MEASUREMENT ---


            # --- 1. RUNTIME FIX ---
            if current_time > 3600:
                app.logger.info(f"AI Engine: Simulation time {current_time}s > 3600s. Stopping simulation.")
                break 
            
            # --- 2. AI & ROUTING LOGIC (Runs every 10s for speed) ---
            if int(current_time) % 10 == 0:
            
                # --- AI 1: "PRESSURE" SMART TRAFFIC LIGHT LOGIC (REMOVED) ---
                
                # --- DIGITAL TWIN (Graph Weight Update) ---
                edge_heatmap_data = [] # For the heatmap

                all_sumo_edges = traci.edge.getIDList()
                for edge_id in all_sumo_edges:
                    if edge_id in edge_id_to_uv:
                        u, v = edge_id_to_uv[edge_id]

                        # --- NEW THREAD-SAFE LOGIC ---
                        # 1. Check if graph 'G' says this edge is an incident
                        if G.edges[u, v, edge_id].get('is_incident') == True:
                            # If so, tell traci to block it
                            traci.edge.setMaxSpeed(edge_id, 0.1)
                        
                        # 2. If 'G' says it's NOT an incident (or was just unblocked by admin)
                        else:
                            # 2a. Tell traci to unblock it (set back to original speed)
                            original_edge = net.getEdge(edge_id)
                            traci.edge.setMaxSpeed(edge_id, original_edge.getSpeed())

                            # 2b. Run AI 2: AUTOMATIC INCIDENT DETECTOR (Halting Cars)
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
                            
                            # 2c. Update travel time from SUMO (only if it's still not an incident)
                            if G.edges[u, v, edge_id].get('is_incident') != True:
                                current_travel_time = traci.edge.getTraveltime(edge_id)
                                G.edges[u, v, edge_id]['travel_time'] = current_travel_time

                                # --- NEW: Add data for heatmap ---
                                original_time = G.edges[u, v, edge_id]['original_travel_time']
                                intensity = current_travel_time / (original_time + 0.001) # +0.001 to avoid zero division
                                node_data = G.nodes[u] # Get coords of the start node
                                edge_heatmap_data.append({
                                    "lat": node_data['lat'],
                                    "lon": node_data['lon'],
                                    "intensity": intensity
                                })

                # --- NEW: Update the global state with heatmap data ---
                ADMIN_STATE["edge_heatmap_data"] = edge_heatmap_data
                
                # --- AI HEARTBEAT NOW GOES TO ADMIN LOG ---
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
ai_thread = threading.Thread(target=update_live_traffic, daemon=True)
ai_thread.start()
time.sleep(3) # Give SUMO time to boot

# --- NEW: Ensure SUMO closes when Flask stops ---
@atexit.register
def cleanup_sumo():
    """
    This function is automatically called when the Flask app is stopped.
    """
    app.logger.info("Flask server shutting down... closing Traci and SUMO.")
    try:
        traci.close()
    except Exception as e:
        app.logger.error(f"Error closing Traci: {e}")
# -----------------------------------------------

# Helper function to find the closest node ID (FIXED and SAFER)
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

        total_distance_meters = nx.path_weight(G, route, weight='length')
        
        route_coords = [[G.nodes[node]['lat'], G.nodes[node]['lon']] for node in route]
        
        # --- REMOVED ROUTE COUNTER ---
        
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
    """
    (FIXED) Updated to be thread-safe AND only count 1 incident per report.
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

    # --- 2. FIND NEAREST EDGE (This part is correct) ---
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
            
        # --- NEW: Add timestamp to history ---
        with history_lock:
            INCIDENT_HISTORY.append(time.time())

        # --- 3. APPLY CRITICAL WEIGHT (THREAD-SAFE) ---
        CRITICAL_COST = 999999 
        edges_blocked = 0
        
        edge_id = edge_to_block.getID()

        # --- Block the primary edge ---
        if edge_id in edge_id_to_uv:
            u, v = edge_id_to_uv[edge_id]
            G[u][v][edge_id]['travel_time'] = CRITICAL_COST
            G[u][v][edge_id]['is_incident'] = True
            edges_blocked += 1
            app.logger.info(f"INCIDENT (Manual): Flagged primary edge {edge_id}")

        # --- (REMOVED) Block the INVERSE edge ---

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
# --- NEW ADMIN API ENDPOINTS ---
# ==========================================================

@app.route('/admin/dashboard_data')
def get_dashboard_data():
    """
    Provides all live data for the admin dashboard.
    """
    # 1. Get all active incidents from the graph
    incidents = []
    try:
        for u, v, key, data in G.edges(keys=True, data=True):
            if data.get('is_incident') == True:
                node_data = G.nodes[u]
                incidents.append({
                    "edge_id": key,
                    "lat": node_data['lat'],
                    "lon": node_data['lon']
                })
    except Exception as e:
        app.logger.error(f"Error reading graph incidents: {e}")

    # 2. Get heatmap data (from AI thread)
    heatmap_data = ADMIN_STATE.get("edge_heatmap_data", [])

    return jsonify({
        "stats": {
            "total_incidents_reported": len(INCIDENT_HISTORY) # Use history length
        },
        "incidents": incidents,
        "edge_heatmap_data": heatmap_data # Send heatmap data
    })

# --- NEW LOGGING ENDPOINT ---
@app.route('/admin/get_logs')
def get_logs():
    """Returns the latest captured log messages."""
    with log_lock:
        logs = list(log_queue)
    return jsonify({"logs": logs})

# --- NEW HISTORY ENDPOINT ---
@app.route('/admin/incident_history')
def get_incident_history():
    """Returns the list of incident timestamps."""
    with history_lock:
        history = list(INCIDENT_HISTORY)
    return jsonify({"history": history})


@app.route('/admin/unblock_edge', methods=['POST'])
def unblock_edge():
    """
    Lets an admin manually unblock an edge.
    The AI thread will see this change and unblock it in SUMO.
    """
    data = request.get_json()
    edge_id = data.get('edge_id')

    if not edge_id or edge_id not in edge_id_to_uv:
        return jsonify({"status": "error", "message": "Invalid edge_id"}), 404

    try:
        u, v = edge_id_to_uv[edge_id]
        G.edges[u, v, edge_id]['is_incident'] = False
        
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
            G.edges[inv_u, inv_v, inverse_edge_id]['is_incident'] = False
            inv_edge = net.getEdge(inverse_edge_id)
            inv_travel_time = inv_edge.getLength() / (inv_edge.getSpeed() + 0.001)
            G.edges[inv_u, inv_v, inverse_edge_id]['travel_time'] = inv_travel_time

        app.logger.info(f"--- ADMIN: Manually flagged edge {edge_id} for unblocking ---")
        return jsonify({"status": "success", "message": f"Edge {edge_id} flagged for unblocking."})

    except Exception as e:
        app.logger.error(f"Error unblocking edge: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# --- NEW: LATENCY ENDPOINT ---
@app.route('/status')
def get_status():
    """
    Provides live status, including Traci-SUMO command latency.
    """
    global g_traci_latency_ms
    return jsonify({
        "status": "success",
        "traci_latency_ms": g_traci_latency_ms
    }), 200

# ==========================================================
# --- ROUTES TO SERVE THE ADMIN WEB PAGE ---
# ==========================================================

# This route serves the main admin.html page
@app.route('/admin')
def serve_admin_page():
    # We tell Flask to look one directory UP (../) and then into the 'admin' folder
    return send_from_directory(os.path.join(os.path.dirname(__file__), '..', 'admin'), 'admin.html')

# This route serves the .css and .js files (like admin.js and admin.css)
@app.route('/admin/<path:filename>')
def serve_admin_static(filename):
    return send_from_directory(os.path.join(os.path.dirname(__file__), '..', 'admin'), filename)

# ==========================================================
    
if __name__ == '__main__':
    # --- THIS BLOCK IS NOW CLEAN ---
    # The logging setup at the top of the file (lines 31-47)
    # is all that is needed.
    app.run(debug=True, host='0.0.0.0', port=5000)