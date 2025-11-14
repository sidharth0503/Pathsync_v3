/*
 * admin.js
 * This file contains all the logic for the admin dashboard.
 */

const BACKEND_URL = 'http://127.0.0.1:5000';
let map;
let incidentLayerGroup;
let heatmapLayer; // For our heatmap
let logBox;
let isCreatingIncident = false; // NEW: State for incident creation

// Wait for the HTML document to load before running our code
document.addEventListener('DOMContentLoaded', () => {
    
    console.log('Admin Dashboard Loaded. Initializing...');

    // Grab the log box element
    logBox = document.getElementById('log-box');
    
    // 1. Initialize Map
    const mapContainer = document.getElementById('map'); // Get map container
    map = L.map(mapContainer).setView([12.30, 76.60], 13);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; <a href="http://osm.org/copyright">OpenStreetMap</a>'
    }).addTo(map);

    // Create a layer group to hold incident markers
    incidentLayerGroup = L.layerGroup().addTo(map);

    // 2. Initialize Heatmap Layer
    heatmapLayer = L.heatLayer([], {
        radius: 8,
        blur: 8,
        max: 5.0, 
        maxZoom: 17,
        minOpacity: 0.5,
        gradient: { 0.2: 'blue', 0.4: 'lime', 0.7: 'yellow', 1.0: 'red' }
    });

    console.log('Map and Heatmap initialized successfully');

    // 3. Set up Toggle Switches
    const heatmapToggle = document.getElementById('heatmap-toggle');
    const createIncidentToggle = document.getElementById('create-incident-toggle');

    heatmapToggle.addEventListener('change', () => {
        if (heatmapToggle.checked) {
            map.addLayer(heatmapLayer);
            console.log('Heatmap layer ADDED');
        } else {
            map.removeLayer(heatmapLayer);
            console.log('Heatmap layer REMOVED');
        }
    });

    // --- NEW: Create Incident Toggle Logic ---
    createIncidentToggle.addEventListener('change', () => {
        isCreatingIncident = createIncidentToggle.checked;
        if (isCreatingIncident) {
            mapContainer.classList.add('creating-incident'); // Add crosshair cursor
            console.log('Create Incident mode ENABLED');
        } else {
            mapContainer.classList.remove('creating-incident'); // Remove crosshair
            console.log('Create Incident mode DISABLED');
        }
    });

    // --- NEW: Map Click Listener for Creating Incidents ---
    map.on('click', (e) => {
        // Only run if the create incident toggle is on
        if (isCreatingIncident) {
            const { lat, lng } = e.latlng;
            console.log(`Map clicked in create mode: ${lat}, ${lng}`);
            
            // Ask for confirmation
            if (confirm(`Create new incident at ${lat.toFixed(4)}, ${lng.toFixed(4)}?`)) {
                createIncident(`${lat},${lng}`);
            }
            
            // Turn off the toggle after clicking
            createIncidentToggle.checked = false;
            isCreatingIncident = false;
            mapContainer.classList.remove('creating-incident');
        }
    });
    
    // 4. Start fetching data
    fetchDashboardData(); // Fetch immediately on load
    fetchLogs(); // Fetch logs on load
    
    setInterval(fetchDashboardData, 5000); // Then fetch every 5 seconds
    setInterval(fetchLogs, 3000); // Fetch logs every 3 seconds
});


/**
 * Fetches the latest data from the backend API.
 */
async function fetchDashboardData() {
    try {
        const response = await fetch(`${BACKEND_URL}/admin/dashboard_data`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();

        // console.log('Received data:', data); // Uncomment for debugging

        // --- 1. Update Stats ---
        updateStats(data.incidents); // Pass incidents for live count

        // --- 2. Update Map with Incidents ---
        updateMap(data.incidents); 

        // --- 3. Update Heatmap ---
        updateHeatmap(data.edge_heatmap_data);

    } catch (error) {
        console.error('Failed to fetch dashboard data:', error);
    }
}

/**
 * Updates the stat cards safely.
 */
function updateStats(incidents) {
    const incidentsStat = document.getElementById('incidents-stat');

    if (incidentsStat) {
        // Use the LIVE count from the incidents array length
        incidentsStat.textContent = incidents?.length ?? 0;
    }
}

/**
 * Fetches the latest logs from the backend.
 */
async function fetchLogs() {
    try {
        const response = await fetch(`${BACKEND_URL}/admin/get_logs`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const logData = await response.json();
        updateLogBox(logData.logs);

    } catch (error) {
        console.error('Failed to fetch logs:', error);
    }
}

/**
 * Updates the log box with new log messages.
 */
function updateLogBox(logs) {
    if (logBox) {
        // Join all log messages with a newline and update
        logBox.textContent = logs.join('\n');
        // Automatically scroll to the bottom
        logBox.scrollTop = logBox.scrollHeight;
    }
}


/**
 * Updates the map with live incident markers.
 */
function updateMap(incidents) {
    incidentLayerGroup.clearLayers(); // Clear all old markers

    // Define a custom red icon for incidents
    const redIcon = L.icon({
        iconUrl: 'https://cdn.rawgit.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-red.png',
        shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png',
        iconSize: [25, 41],
        iconAnchor: [12, 41],
        popupAnchor: [1, -34],
        shadowSize: [41, 41]
    });

    incidents.forEach(incident => {
        const latLon = [incident.lat, incident.lon];
        
        // Create the HTML for the popup
        const popupContent = `
            <div style="padding: 5px;">
                <strong><i class="bi bi-cone-striped"></i> Active Incident</strong><br>
                Edge ID: <code>${incident.edge_id}</code>
                <hr style="margin: 8px 0;">
                <button class="btn btn-danger btn-sm w-100" onclick="unblockEdge('${incident.edge_id}')">
                    <i class="bi bi-unlock-fill"></i> Unblock Road
                </button>
            </div>
        `;

        // Create the marker and add it to our layer group
        const marker = L.marker(latLon, { icon: redIcon })
            .bindPopup(popupContent);
        
        incidentLayerGroup.addLayer(marker);
    });
}

/**
 * Updates the congestion heatmap with new data.
 */
function updateHeatmap(heatmapData) {
    if (!heatmapData || heatmapData.length === 0) {
        heatmapLayer.setLatLngs([]); // Clear the map if no data
        return;
    }

    // Convert our edge data into [lat, lon, intensity] points
    const heatPoints = heatmapData.map(edge => {
        // Intensity = how much *worse* is the traffic? (min of 0.1)
        // We cap it at 5x to prevent one-hotspot-takes-all
        const intensity = Math.min(edge.intensity, 5); 
        return [edge.lat, edge.lon, intensity];
    });

    heatmapLayer.setLatLngs(heatPoints);
    // console.log(`Heatmap updated with ${heatPoints.length} points.`);
}


/**
 * Public function to be called by marker popups to unblock an edge.
 */
async function unblockEdge(edge_id) {
    console.log(`Attempting to unblock edge: ${edge_id}`);
    try {
        const response = await fetch(`${BACKEND_URL}/admin/unblock_edge`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ edge_id: edge_id })
        });

        const result = await response.json();

        if (response.ok && result.status === 'success') {
            alert('Edge unblocked successfully! Refreshing data.');
            map.closePopup(); // Close the popup
            fetchDashboardData(); // Manually trigger a refresh to update the map
        } else {
            alert(`Failed to unblock edge: ${result.message}`);
        }
    } catch (error) {
        console.error('Error in unblockEdge:', error);
        alert('An error occurred while trying to unblock the edge.');
    }
}
// Attach to the window object so the HTML 'onclick' can find it
window.unblockEdge = unblockEdge;

// --- *** NEW FUNCTION: Create Incident *** ---
async function createIncident(location_name) {
    console.log(`Creating new incident at: ${location_name}`);
    try {
        const response = await fetch(`${BACKEND_URL}/report`, { // Use the EXISTING /report endpoint
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ 
                location_name: location_name,
                type: 'Admin Incident' // Send a specific type
            })
        });

        const result = await response.json();

        if (response.ok && result.status === 'success') {
            alert('Incident created successfully! Refreshing data.');
            fetchDashboardData(); // Instantly refresh the map to show the new marker
        } else {
            alert(`Failed to create incident: ${result.message}`);
        }
    } catch (error) {
        console.error('Error in createIncident:', error);
        alert('An error occurred while creating the incident.');
    }
}
// This function doesn't need to be on the window object