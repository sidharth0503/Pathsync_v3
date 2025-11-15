**Pathsync v3: An Adaptive Urban Traffic Management System**

**Status:** Stable

**1\. Project Overview**

Pathsync v3 is a full-stack, intelligent traffic management solution designed to address urban congestion. It moves beyond static, timer-based systems by implementing a real-time **Adaptive Control** system.

The core of Pathsync is a **"Digital Twin"** architecture. A live, microscopic traffic simulation (SUMO) acts as the "real world," and a parallel graph model in the backend (app.py) acts as the "Digital Twin." This twin continuously learns and updates its understanding of traffic flow, allowing it to make intelligent, real-time decisions.

The system provides **Dual Control** over the network by:

- **Providing citizens** with the fastest, congestion-aware routes via a mobile app (Home.tsx).
- **Providing administrators** with a live monitoring dashboard (admin.html) to manage incidents and visualize network health.

**2\. Core Architecture: The "Digital Twin"**

The system's "brain" (app.py) runs two models in parallel:

- **The "Real World" (SUMO):** An instance of the SUMO traffic simulator runs live vehicle traffic, generating real-time data on speed, vehicle count, and travel time.
- **The "Digital Twin" (NetworkX):** A Python NetworkX graph (G) mirrors the SUMO road map.

A dedicated background thread (update_live_traffic) acts as the synchronization layer. It constantly copies real-time travel_time data from SUMO's roads and updates the **edge weights (cost)** of the NetworkX graph.

All routing queries from the mobile app are run against this **live, dynamic graph**, ensuring that all route recommendations are based on the _current_ state of the network, not a static map.

**3\. Intelligent Control Systems (AI Implementation)**

Pathsync's intelligence is not a single, pre-trained model. It is an **Adaptive Control System** that operates on continuous, real-time feedback loops, adhering to principles of Reinforcement Learning (RL).

**3.1. Loop 1: Continuous State Learning (The Digital Twin)**

The system is in a state of **continuous learning**.

- **Mechanism:** The update_live_traffic thread constantly updates the travel_time (edge weights) of the Digital Twin graph G.
- **Intelligence:** This graph G acts as the system's learned **cost function**. The AI doesn't just know the _shortest_ path; it learns the _fastest_ (least-cost) path by observing the live environment.

**3.2. Loop 2: Autonomous Anomaly Detection (Reactive AI)**

The system can autonomously **monitor and react** to unforeseen events.

- **Mechanism:** The AI thread runs an **Automatic Incident Detector** that monitors traffic for gridlock conditions (defined by JAM_HALT_THRESHOLD and JAM_TIME_THRESHOLD).
- **Intelligence:** If a road is detected as gridlocked (e.g., 10+ cars stopped for 30s), the AI autonomously applies a **CRITICAL_COST (999999)** to that road's edge in the graph G. This is an immediate, decisive action to remove a failing node from the routable network.

**3.3. Loop 3: Dynamic Route Optimization (Proactive AI)**

This is the control layer where the learned information is used to influence driver behavior.

- **Mechanism:** The mobile app's /route endpoint executes **Dijkstra's algorithm** (nx.shortest_path) on the **live, weighted** Digital Twin.
- **Intelligence:** The system calculates the fastest path based on the _current learned state_ (from Loop 1) and any _active incidents_ (from Loop 2). Every re-route serves as a feedback mechanism, proactively managing traffic flow.

**4\. Technology Stack**

- **Backend & AI Engine (Python, Flask, NetworkX):** Hosts the Digital Twin, runs all AI logic, and serves the REST API.
- **Simulation (Eclipse SUMO, traci, sumolib):** Provides the realistic, microscopic traffic environment and data stream.
- **Mobile Frontend (React, Ionic, Capacitor):** User-facing application for live GPS, route display, and incident reporting.
- **Admin Dashboard (HTML, CSS, Vanilla JavaScript):** Real-time monitoring dashboard for administrators.
- **Data Visualization (Leaflet.js, Leaflet.heat, Chart.js):** Used in both frontends for map rendering, heatmap display, and charts.
- **Geolocation (Geopy, Nominatim):** Used for geocoding location names (e.g., "Mandi Mohalla") to Lat/Lon.
- **Concurrency (Python threading):** Used to run the AI Engine (Digital Twin sync) in parallel with the Flask web server.
- **Thread Safety (threading.Lock - g_lock):** **Critical component** that prevents race conditions by ensuring the AI thread (writing to G) and API threads (reading from G) do not conflict.

**5\. System Components & File Breakdown**

**5.1. app.py (The Backend Server & AI Engine)**

This is the central "brain" of the project. It is responsible for:

- **Initialization:** Loads the SUMO network into the NetworkX graph G and initializes all thread-safe locks.
- **update_live_traffic() (AI Thread):** Runs in the background to:

- Advance the SUMO simulation (traci.simulationStep()).
- Update the **Digital Twin** graph G with live travel times.
- Run the **Automatic Incident Detector** and block jammed roads.
- Update live **traffic signal states** for the admin panel.
- Filter and calculate **heatmap intensity** data.

- **API Endpoints:** Hosts the Flask API to communicate with the frontends.

**5.2. Home.tsx (The Mobile App)**

This is the citizen-facing application.

- Uses @capacitor/geolocation to get the user's live GPS position and heading.
- Uses react-leaflet to display the map, live location (rotating arrow), and route.
- Calls the /route endpoint to fetch and display the fastest path (Polyline).
- Implements an **automatic re-routing** feature if the user deviates >50m from the path.
- Allows users to manually submit incident reports to the /report endpoint.

**5.3. admin.html / admin.js (The Admin Dashboard)**

This is the "command center" for administrators.

- **Asynchronous Polling:** admin.js continuously polls the backend API endpoints every 2-5 seconds.
- **Live Map (Leaflet.js):**
- Displays all **active incidents** (both manual and automatic) as red markers.
- Visualizes all **traffic signals** in real-time (cycling Red/Yellow/Green).
- Renders a live **congestion heatmap** (Leaflet.heat) based on filtered intensity data.
- **Interactive Controls:**
- Allows admins to **manually create** incidents by clicking the map (/report).
- Allows admins to **resolve** incidents by clicking a marker (/admin/unblock_edge).
- **Live Charts (Chart.js):** Displays cumulative graphs for incident reports and resolutions.
- **Live Logs:** Shows the app.logger output in real-time.

**6\. Setup and Installation**

**Prerequisites**

- Python 3.8+
- Eclipse SUMO (latest version)
- The SUMO_HOME environment variable must be set (e.g., export SUMO_HOME="/usr/share/sumo").

**Step 1: Clone Repository**

git clone \[YOUR_REPOSITORY_URL\]  
cd \[REPOSITORY_FOLDER\]  
<br/>

**Step 2: Install Python Dependencies**

\# It is recommended to use a virtual environment  
python -m venv venv  
source venv/bin/activate  # (or .\\venv\\Scripts\\activate on Windows)  
<br/>\# Install required packages  
pip install Flask flask_cors geopy networkx  
<br/>

**Step 3: Run the Backend Server**

This single command starts the Flask server, which in turn launches the AI thread and the SUMO simulation.

python app.py  
<br/>

The server will be running at <http://127.0.0.1:5000/>

**Step 4: Access the Admin Dashboard**

Open your web browser and navigate to:

<http://127.0.0.1:5000/admin>

**Step 5: Run the Mobile App**

- Navigate to the mobile app's directory.
- Install dependencies: npm install
- **Crucially:** Open Home.tsx and change the YOUR_COMPUTER_IP constant to your computer's local network IP (e.g., 192.168.1.10), not 127.0.0.1.
- Run the app in your browser (ionic serve) or build for a device (ionic cap run android).

**7\. API Endpoint Reference**

- **POST /route (Mobile):** Calculates the fastest route from A to B using the live graph.
- **POST /report (Mobile/Admin):** Reports a new incident at a given location, blocking the road.
- **GET /status (Admin):** Returns the live SUMO-Traci connection latency.
- **GET /admin/dashboard_data (Admin):** Returns a JSON object with all incidents, traffic lights, and heatmap data.
- **GET /admin/get_logs (Admin):** Returns the 50 most recent system log messages.
- **GET /admin/incident_history (Admin):** Returns a list of timestamps for incident creation.
- **GET /admin/resolved_history (Admin):** Returns a list of timestamps for incident resolution.
- **POST /admin/unblock_edge (Admin):** Unblocks a road and restores its original travel time.

**8\. Author**

This project was developed by Sidharth S.