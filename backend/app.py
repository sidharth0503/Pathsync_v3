import os
import sys
import subprocess
import time
from flask import Flask, jsonify, request
import threading
import networkx as nx
import traci
import sumolib 

# --- 1. CONFIGURE FLASK APP ---
app = Flask(__name__)

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
    "--end", "3600"
]

# --- 6. LOAD/CREATE YOUR ROUTING GRAPH (THE NEW WAY) ---
# (Moving this *before* the AI thread, so the graph G exists)
print("Loading SUMO map into NetworkX graph...")

net = sumolib.net.readNet(sumo_net_file)
G = nx.MultiDiGraph() 
edge_id_to_uv = {} # <-- THIS IS THE FIX (PART 1)

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
    
    # --- THIS IS THE FIX (PART 2) ---
    # Store a simple lookup: "edge_id" -> (from_node, to_node)
    edge_id_to_uv[edge_id] = (from_node, to_node)

print("NetworkX graph 'G' created successfully from SUMO net.")


# --- 5. DEFINE "AI ENGINE" HEARTBEAT FUNCTION ---
def update_live_traffic():
    """
    The main "AI Engine" loop.
    This connects to SUMO and acts as the simulation's heartbeat.
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
            # --- "Heartbeat" ---
            traci.simulationStep()
            current_time = traci.simulation.getTime()

            # --- "DIGITAL TWIN" LOGIC (THE EFFICIENT O(n) FIX) ---
            all_sumo_edges = traci.edge.getIDList()
            
            for edge_id in all_sumo_edges:
                # 1. Get live travel time
                current_travel_time = traci.edge.getTraveltime(edge_id)
                
                # 2. Check if this edge is in our lookup map
                if edge_id in edge_id_to_uv:
                    # 3. Get the (u, v) nodes from the map
                    u, v = edge_id_to_uv[edge_id]
                    
                    # 4. Update the graph edge directly
                    G.edges[u, v, edge_id]['travel_time'] = current_travel_time
                        
            # --- "DIGITAL TWIN" LOGIC ENDS ---

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

# --- 8. DEFINE API ENDPOINTS (THE NEW WAY) ---
@app.route('/')
def index():
    return "Pathsync v3 Backend is running!"

@app.route('/route', methods=['POST'])
def get_route():
    try:
        data = request.get_json()
        start_lat = data['start_lat']
        start_lon = data['start_lon']
        end_lat = data['end_lat']
        end_lon = data['end_lon']

        start_x, start_y = net.convertLonLat2XY(start_lon, start_lat)
        end_x, end_y = net.convertLonLat2XY(end_lon, end_lat)

        start_node = net.getNearestNodes(start_x, start_y)[0][0].getID()
        end_node = net.getNearestNodes(end_x, end_y)[0][0].getID()

        route = nx.shortest_path(G, start_node, end_node, weight='travel_time')
        
        route_coords = [[G.nodes[node]['lat'], G.nodes[node]['lon']] for node in route]
        
        return jsonify({
            "status": "success",
            "route_coords": route_coords
        }), 200

    except nx.NetworkXNoPath:
        return jsonify({"status": "error", "message": "No valid path found."}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500