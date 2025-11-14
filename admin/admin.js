/*
 * admin.js
 * This file contains all the logic for the admin dashboard.
 */

const BACKEND_URL = 'http://127.0.0.1:5000';
let map;
let incidentLayerGroup;
let heatmapLayer;
let logBox;
let incidentHistoryChart; // <-- Chart variable
let isCreatingIncident = false; 

document.addEventListener('DOMContentLoaded', () => {
    
    console.log('Admin Dashboard Loaded. Initializing...');

    logBox = document.getElementById('log-box');
    
    // 1. Initialize Map
    const mapContainer = document.getElementById('map');
    map = L.map(mapContainer).setView([12.30, 76.60], 13);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; <a href="http://osm.org/copyright">OpenStreetMap</a>'
    }).addTo(map);

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

    // 3. --- NEW: Initialize Incident History Chart ---
    const historyCtx = document.getElementById('history-chart').getContext('2d');
    incidentHistoryChart = new Chart(historyCtx, {
        type: 'line',
        data: {
            // No 'labels' array needed here for time-series
            datasets: [{
                label: 'Incidents Reported',
                data: [], // Will be {x, y} objects
                backgroundColor: 'rgba(220, 53, 69, 0.1)',
                borderColor: 'rgba(220, 53, 69, 1)',
                borderWidth: 2,
                fill: true,
                tension: 0.1
            }]
        },
        options: {
            scales: {
                // --- NEW: X-axis is now 'time' ---
                x: {
                    type: 'time',
                    time: {
                        unit: 'minute',
                        tooltipFormat: 'HH:mm:ss', // Format for tooltips
                        displayFormats: {
                            minute: 'HH:mm:ss' // Format for the axis label
                        }
                    },
                    title: {
                        display: true,
                        text: 'Time of Incident'
                    }
                },
                // --- Y-axis is unchanged ---
                y: {
                    beginAtZero: true,
                    ticks: {
                        stepSize: 1 // Only show whole numbers (1, 2, 3)
                    },
                    title: {
                        display: true,
                        text: 'Total Incidents'
                    }
                }
            }
        }
    });
    console.log('Incident History chart initialized');

    // 4. Set up Toggle Switches
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

    map.on('click', (e) => {
        if (isCreatingIncident) {
            const { lat, lng } = e.latlng;
            console.log(`Map clicked in create mode: ${lat}, ${lng}`);
            
            if (confirm(`Create new incident at ${lat.toFixed(4)}, ${lng.toFixed(4)}?`)) {
                createIncident(`${lat},${lng}`);
            }
            
            createIncidentToggle.checked = false;
            isCreatingIncident = false;
            mapContainer.classList.remove('creating-incident');
        }
    });
    
    // 5. Start fetching data
    fetchDashboardData(); // Fetch live map data
    fetchLogs(); // Fetch log data
    fetchIncidentHistory(); // <-- NEW: Fetch history data
    fetchStatusData(); // <-- NEW: Fetch latency on load
    
    setInterval(fetchDashboardData, 5000); 
    setInterval(fetchLogs, 3000); 
    setInterval(fetchIncidentHistory, 5000); // <-- NEW: Refresh history
    setInterval(fetchStatusData, 2000); // <-- NEW: Poll latency every 2 seconds
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

        // --- 1. Update Map with Incidents ---
        updateMap(data.incidents); 
        // --- 2. Update Heatmap ---
        updateHeatmap(data.edge_heatmap_data);
        // --- 3. (REMOVED) updateStats ---

    } catch (error) {
        console.error('Failed to fetch dashboard data:', error);
    }
}

/**
 * --- NEW: Fetches the incident history from the backend. ---
 */
async function fetchIncidentHistory() {
    try {
        const response = await fetch(`${BACKEND_URL}/admin/incident_history`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const histData = await response.json();
        updateHistoryChart(histData.history);

    } catch (error) {
        console.error('Failed to fetch incident history:', error);
    }
}

/**
 * --- FIX: Updates the chart with the FULL history. ---
 */
function updateHistoryChart(timestamps) {
    if (!incidentHistoryChart) return;

    // --- REMOVED this line: const lastTenTimestamps = timestamps.slice(-10); ---

    // --- Process data into {x, y} pairs ---
    // Changed to use the full 'timestamps' array
    const chartData = timestamps.map((ts, index) => {
        return {
            x: ts * 1000, // Chart.js needs timestamps in milliseconds
            y: index + 1  // Cumulative count (1, 2, 3...)
        };
    });

    // Update the chart
    incidentHistoryChart.data.datasets[0].data = chartData;
    incidentHistoryChart.update();
}

/**
 * --- NEW: Fetches the latest system status (e.g., latency) ---
 */
async function fetchStatusData() {
    try {
        const response = await fetch(`${BACKEND_URL}/status`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        if (data.status === 'success') {
            updateLatencyStat(data.traci_latency_ms);
        }
    } catch (error) {
        console.error('Failed to fetch status data:', error);
        updateLatencyStat(null); // Show an error state
    }
}

/**
 * --- NEW: Updates the latency stat card in the DOM. ---
 */
function updateLatencyStat(latency) {
    const statElement = document.getElementById('latency-stat');
    if (!statElement) return;

    if (latency === null || typeof latency === 'undefined') {
        statElement.textContent = 'N/A';
        statElement.classList.add('text-danger'); // Show an error
    } else {
        statElement.textContent = latency.toFixed(2);
        statElement.classList.remove('text-danger');
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
        logBox.textContent = logs.join('\n');
        logBox.scrollTop = logA.scrollHeight;
    }
}


/**
 * Updates the map with live incident markers.
 */
function updateMap(incidents) {
    incidentLayerGroup.clearLayers(); 

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

    const heatPoints = heatmapData.map(edge => {
        const intensity = Math.min(edge.intensity, 5); 
        return [edge.lat, edge.lon, intensity];
    });

    heatmapLayer.setLatLngs(heatPoints);
}


/**
 * Public function to be called by marker popups to unblock an edge.
 */
async function unblockEdge(edge_id) {
    console.log(`Attempting to unblock edge: ${edge_id}`);
    try {
        const response = await fetch(`${BACKEND_URL}/admin/unblock_edge`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ edge_id: edge_id })
        });

        const result = await response.json();

        if (response.ok && result.status === 'success') {
            alert('Edge unblocked successfully! Refreshing data.');
            map.closePopup(); 
            fetchDashboardData(); 
            fetchIncidentHistory(); // <-- NEW: Refresh history chart too
        } else {
            alert(`Failed to unblock edge: ${result.message}`);
        }
    } catch (error) {
        console.error('Error in unblockEdge:', error);
        alert('An error occurred while trying to unblock the edge.');
    }
}
window.unblockEdge = unblockEdge;

// --- NEW FUNCTION: Create Incident ---
async function createIncident(location_name) {
    console.log(`Creating new incident at: ${location_name}`);
    try {
        const response = await fetch(`${BACKEND_URL}/report`, { // Use the EXISTING /report endpoint
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                location_name: location_name,
                type: 'Admin Incident' // Send a specific type
            })
        });

        const result = await response.json();

        if (response.ok && result.status === 'success') {
            alert('Incident created successfully! Refreshing data.');
            fetchDashboardData(); // Instantly refresh the map
            fetchIncidentHistory(); // <-- NEW: Refresh history chart too
        } else {
            alert(`Failed to create incident: ${result.message}`);
        }
    } catch (error) {
        console.error('Error in createIncident:', error);
        alert('An error occurred while creating the incident.');
    }
}