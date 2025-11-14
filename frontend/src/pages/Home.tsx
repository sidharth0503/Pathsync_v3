import React, { useState, useEffect } from 'react';
import { IonContent, IonHeader, IonPage, IonTitle, IonToolbar, IonInput, IonButton, IonGrid, IonRow, IonCol, IonLabel, IonSpinner, IonSegment, IonSegmentButton } from '@ionic/react';
import { MapContainer, TileLayer, Marker, Polyline, useMapEvents, useMap, CircleMarker } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
import L, { LatLngExpression } from 'leaflet';
import { Geolocation } from '@capacitor/geolocation';

/* eslint-disable @typescript-eslint/ban-ts-comment */
/* eslint-disable @typescript-eslint/no-unused-vars */

// --- Fix for missing marker icons ---
// @ts-ignore
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://unpkg.com/leaflet@1.7.1/dist/images/marker-icon-2x.png',
  iconUrl: 'https://unpkg.com/leaflet@1.7.1/dist/images/marker-icon.png',
  shadowUrl: 'https://unpkg.com/leaflet@1.7.1/dist/images/marker-shadow.png',
});

// --- NEW: Custom Arrow Icon for Live Position ---
const arrowIcon = L.divIcon({
  className: 'leaflet-arrow-icon', // Use this class to style if needed
  html: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="32" height="32" style="transform: rotate(var(--arrow-heading, 0deg)); transition: transform 0.2s ease-out;">
    <path fill="#0056b3" d="M12 2L2.5 21.5 12 17 21.5 21.5z" stroke="#FFFFFF" stroke-width="1.5" />
  </svg>`,
  iconSize: [32, 32],
  iconAnchor: [16, 16], // Anchor in the center
});
// -----------------------------------------------------------------------------

// --- Center coordinates for Mysuru ---
const MY_CENTER_LAT = 12.30;
const MY_CENTER_LON = 76.60;

// --- PLACEHOLDER LOGO (Replace with your base64 string) ---
const LOGO_DATA_URL = 'data:image/png;base64,PLACEHOLDER_LOGO_BASE64_STRING';


const Home: React.FC = () => {
  // State management
  const [inputMode, setInputMode] = useState<'name' | 'map'>('name');
  const [startName, setStartName] = useState<string>("Vidya Vardhaka College of Engineering");
  const [endName, setEndName] = useState<string>("Columbia Asia Hospital");

  const [startLat, setStartLat] = useState<number>(MY_CENTER_LAT + 0.01);
  const [startLon, setStartLon] = useState<number>(MY_CENTER_LON - 0.01);
  const [endLat, setEndLat] = useState<number>(MY_CENTER_LAT - 0.01);
  const [endLon, setEndLon] = useState<number>(MY_CENTER_LON + 0.01);

  const [routeCoords, setRouteCoords] = useState<[number, number][]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const [totalTime, setTotalTime] = useState<number | null>(null);
  const [totalDistance, setTotalDistance] = useState<number | null>(null);

  const [livePosition, setLivePosition] = useState<[number, number] | null>(null);
  const [isFollowing, setIsFollowing] = useState<boolean>(true);
  const [reportMessage, setReportMessage] = useState<string | null>(null);

  const [incidentLocation, setIncidentLocation] = useState<[number, number] | null>(null);
  const [isDroppingPin, setIsDroppingPin] = useState<boolean>(false);
  const [mapClickState, setMapClickState] = useState<'source' | 'destination' | 'done'>('source');

  // --- NEW: State for Compass Heading ---
  const [heading, setHeading] = useState<number>(0);

  // --- FIXED: Backend URLs for Android emulator ---
  const YOUR_COMPUTER_IP = "10.60.212.227"; // This should be your computer's IP
  const BACKEND_URL = `http://${YOUR_COMPUTER_IP}:5000/route`;
  const REPORT_URL = `http://${YOUR_COMPUTER_IP}:5000/report`;

  // --- FIXED: GPS initialization with better error handling ---
  useEffect(() => {
    let watchId: string | null = null;

    const initializeLocation = async () => {
      try {
        console.log("Checking location permissions...");
        let permStatus = await Geolocation.checkPermissions();

        if (permStatus.location !== 'granted') {
          console.log("Requesting location permissions...");
          permStatus = await Geolocation.requestPermissions();
        }

        if (permStatus.location === 'granted') {
          console.log("Location permission granted, starting watch...");

          watchId = await Geolocation.watchPosition(
            { enableHighAccuracy: true, timeout: 10000, maximumAge: 5000 },
            (position, err) => {
              if (err) {
                console.error("GPS Error:", err);
                setError(`GPS Error: ${err.message}`);
                return;
              }
              if (position?.coords) {
                // console.log("Position update:", position.coords.latitude, position.coords.longitude);
                setLivePosition([position.coords.latitude, position.coords.longitude]);
                setError(null);
              }
            }
          );
        } else {
          console.error("Location permission denied");
          setError("Location permission denied. Please enable location in settings.");
        }
      } catch (e) {
        console.error("Location initialization error:", e);
        setError(`Location error: ${(e as Error).message}`);
      }
    };

    initializeLocation();

    // Cleanup function
    return () => {
      if (watchId) {
        Geolocation.clearWatch({ id: watchId });
      }
    };
  }, []);

  // --- NEW: Compass (Device Orientation) Effect ---
  useEffect(() => {
    const handleOrientation = (event: DeviceOrientationEvent) => {
      // event.alpha is the compass heading (0-360, where 0 is North)
      if (event.alpha !== null) {
        setHeading(event.alpha);
        // Update CSS variable for the icon's rotation
        document.documentElement.style.setProperty('--arrow-heading', `${event.alpha}deg`);
      }
    };

    // Check for support and add listener
    if (window.DeviceOrientationEvent) {
      window.addEventListener('deviceorientation', handleOrientation);
    } else {
      console.log("DeviceOrientationEvent is not supported by this device.");
    }

    return () => {
      // Clean up the listener
      window.removeEventListener('deviceorientation', handleOrientation);
    };
  }, []); // Run once on mount
  // --------------------------------------------------

  // Clear route function
  const clearRoute = () => {
    setRouteCoords([]);
    setTotalTime(null);
    setTotalDistance(null);
    setError(null);
    setReportMessage(null);
    setIncidentLocation(null);
    setIsDroppingPin(false);
    setMapClickState('source');
    setInputMode('name');
  };

  // Fetch route from backend
  const fetchRoute = async (newSource: [number, number] | null = null) => {
    setLoading(true);
    setError(null);

    // --- FIX #3: FIX MAP LOCK ---
    // Set following state immediately for manual routes
    if (newSource) {
        // setIsFollowing(true); // <-- BUGFIX: REMOVED THIS LINE. Do not force follow on auto-reroute.
    } else {
        setIsFollowing(false); // MANUAL route, so unlock map
        setRouteCoords([]);
        setTotalTime(null);
        setTotalDistance(null);
    }
    // ----------------------------

    let finalPayload;

    if (newSource) {
      // --- THIS IS AN AUTOMATIC REROUTE ---
      finalPayload = {
        start_name: `${newSource[0]},${newSource[1]}`,
        end_name: `${endLat},${endLon}`
      };
    } else {
      // --- THIS IS A MANUAL ROUTE REQUEST ---
      if (inputMode === 'name') {
        if (startName.length < 3 || endName.length < 3) {
          setError("Please enter valid location names (3+ characters).");
          setLoading(false);
          return;
        }
        finalPayload = { start_name: startName, end_name: endName };
      } else {
        // --- FIX: Check mapClickState is 'done' ---
        if (mapClickState !== 'done' || (startLat === MY_CENTER_LAT && endLat === MY_CENTER_LAT)) {
          setError("Please finish selecting both Source and Destination on the map.");
          setLoading(false);
          return;
        }
        finalPayload = { start_name: `${startLat},${startLon}`, end_name: `${endLat},${endLon}` };
      }
    }

    try {
      console.log("Fetching route with payload:", finalPayload);
      const response = await fetch(BACKEND_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(finalPayload),
      });

      const data = await response.json();
      console.log("Route response:", data);

      if (response.status === 200 && data.status === 'success') {
        setRouteCoords(data.route_coords);
        setTotalTime(data.total_time_seconds);
        setTotalDistance(data.total_distance_meters);
        setReportMessage(null);
        
        if (!newSource && data.route_coords.length > 0) {
          setStartLat(data.route_coords[0][0]);
          setStartLon(data.route_coords[0][1]);
          setEndLat(data.route_coords[data.route_coords.length - 1][0]);
          setEndLon(data.route_coords[data.route_coords.length - 1][1]);
        }
      } else {
        setError(data.message || 'Unknown routing error.');
      }
    } catch (err) {
      console.error("Network error:", err);
      setError(`Network Error: ${(err as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  // Report incident
  const reportIncident = async () => {
    if (incidentLocation === null) {
      setError("Please click 'Drop Incident Pin' and tap the map first.");
      return;
    }

    const locationPayload = `${incidentLocation[0]},${incidentLocation[1]}`;
    const payload = {
      location_name: locationPayload,
      type: 'Accident',
    };

    setReportMessage("Sending incident report...");

    try {
      console.log("Reporting incident:", payload);
      const response = await fetch(REPORT_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      const data = await response.json();
      console.log("Report response:", data);

      if (response.status === 200) {
        setReportMessage(`INCIDENT CONFIRMED: ${data.message}`);
        setIncidentLocation(null);
        setError(null);
        // Automatically fetch a new route after reporting
        // Use `null` to re-calculate from the original start
        fetchRoute(null);
      } else {
        setReportMessage(`Report Failed: ${data.message}`);
      }
    } catch (err) {
      console.error("Report error:", err);
      setReportMessage("Network Error: Cannot connect to server for reporting.");
    }
  };

  // --- Map Click Handler (FIXED) ---
  const MapClickHandler = () => {
    const map = useMap();
    useMapEvents({
      dragstart: () => {
        setIsFollowing(false);
      },
      click: (e) => {
        const { lat, lng } = e.latlng;
        console.log("Map clicked:", lat, lng);

        if (isDroppingPin) {
          setIncidentLocation([lat, lng]);
          setReportMessage("Incident pin placed. Click 'REPORT' button to confirm.");
          setError(null);
          setIsDroppingPin(false);
          setMapClickState('done'); // Clicks are done, show main card
        } else if (inputMode === 'map') {
          if (mapClickState === 'source') {
            setStartLat(lat);
            setStartLon(lng);
            setEndLat(MY_CENTER_LAT - 0.01);
            setEndLon(MY_CENTER_LON + 0.01);
            setMapClickState('destination'); // Now wait for destination
            setRouteCoords([]);
            setError(null);
          } else if (mapClickState === 'destination') {
            setEndLat(lat);
            setEndLon(lng);
            setMapClickState('done'); // Clicks are done, show main card
            setError(null);
          }
        }
      },
    });
    return null;
  };

  // Follow me component
  const FollowMe: React.FC = () => {
    const map = useMap();
    useEffect(() => {
      if (isFollowing && livePosition) {
        map.setView(livePosition, 15);
      }
    }, [map, livePosition, isFollowing]);
    return null;
  };

  // --- FIXED: Off-route detection (WITH GRACE DISTANCE) ---
  const isRouteActive = routeCoords.length > 0;

  useEffect(() => {
    if (!isRouteActive || !livePosition || routeCoords.length === 0 || loading) {
      return;
    }

    const userLatLng = L.latLng(livePosition[0], livePosition[1]);

    // ==========================================================
    // --- GRACE DISTANCE FIX ---
    // ==========================================================
    const startOfRoute = L.latLng(routeCoords[0][0], routeCoords[0][1]);
    const distanceToStart = userLatLng.distanceTo(startOfRoute);

    const GRACE_DISTANCE_METERS = 500;
    if (distanceToStart > GRACE_DISTANCE_METERS) {
      return; // User is too far from route start, assume it's intentional
    }
    // ==========================================================
    
    let minDistance = Infinity;
    for (const coord of routeCoords) {
      const routePointLatLng = L.latLng(coord[0], coord[1]);
      const distance = userLatLng.distanceTo(routePointLatLng);
      if (distance < minDistance) {
        minDistance = distance;
      }
    }

    const OFF_ROUTE_THRESHOLD = 50; // 50 meters

    if (minDistance > OFF_ROUTE_THRESHOLD) {
      console.log("USER IS OFF-ROUTE! Rerouting...");
      fetchRoute(livePosition); // Pass livePosition to reroute from current spot
    }
  }, [livePosition, isRouteActive, routeCoords, loading]); // Correct dependencies

  // Drop pin handler
  const handleDropPinClick = () => {
    if (isDroppingPin) {
      setIsDroppingPin(false);
      setReportMessage(null);
    } else {
      setIsDroppingPin(true);
      setInputMode('map');
      setReportMessage("Click on the map to drop an incident pin.");
      setError(null);
      setMapClickState('source');
    }
  };

  // --- RE-ADDED: Component to fix map loading bug ---
  const FixMapEffect: React.FC = () => {
    const map = useMap();
    useEffect(() => {
      setTimeout(() => {
        map.invalidateSize();
      }, 100);
    }, [map]);
    return null;
  };

  return (
    <IonPage>
      <IonContent fullscreen style={{ '--background': '#f0f0f0' } as React.CSSProperties}>

        {/* Map Container */}
        <MapContainer
          center={[MY_CENTER_LAT, MY_CENTER_LON]}
          zoom={13}
          style={{ height: '100vh', width: '100vw', position: 'absolute', zIndex: 0 }}
        >
          <TileLayer
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            attribution='&copy; <a href="http://osm.org/copyright">OpenStreetMap</a> contributors'
          />

          <FixMapEffect />
          <MapClickHandler />
          <FollowMe />

          <Marker position={[startLat, startLon]} />
          <Marker position={[endLat, endLon]} />

          {isRouteActive && (
            <Polyline positions={routeCoords} color="#0056b3" weight={6} opacity={0.9} />
          )}

          {incidentLocation && (
            <CircleMarker
              center={incidentLocation}
              radius={8}
              pathOptions={{ color: 'white', fillColor: '#dc3545', fillOpacity: 1.0, weight: 2 }}
            />
          )}

          {/* --- UPDATED: Live User "Blue Dot" is now a rotating Arrow --- */}
          {livePosition && (
            <Marker
              position={livePosition}
              icon={arrowIcon}
              // The rotation is handled by the CSS variable '--arrow-heading'
            />
          )}

        </MapContainer>

        {/* Main Input Card */}
        {!isRouteActive && (inputMode === 'name' || (inputMode === 'map' && mapClickState === 'done')) && (
          <div style={{
            position: 'absolute',
            top: '50%',
            left: '50%',
            transform: 'translate(-50%, -50%)',
            width: 'calc(100% - 40px)',
            maxWidth: '350px',
            zIndex: 10,
            background: 'rgba(255, 255, 255, 0.98)',
            padding: '20px',
            borderRadius: '16px',
            boxShadow: '0 8px 25px rgba(0, 0, 0, 0.2)',
            border: '1px solid #e0e0e0',
          }}>

            <img
              src={LOGO_DATA_URL}
              alt="Pathsync Logo"
              style={{ height: '150px', width: 'auto', margin: '0 auto 15px', display: 'block' }}
            />

            <IonSegment
                value={inputMode}
                onIonChange={(e) => {
                    const newMode = e.detail.value as 'name' | 'map';
                    setInputMode(newMode);
                    setError(null);
                    setReportMessage(null);
                    setIsDroppingPin(false);
                    setIncidentLocation(null);
                    setMapClickState('source');
                }}
                color="primary"
                style={{ marginBottom: '20px', border: '1px solid #0056b3', borderRadius: '10px', '--background': '#f0f0f0' } as React.CSSProperties}
            >
                <IonSegmentButton value="name">
                    <IonLabel style={{fontSize: '0.9em', fontWeight: 'bold', color: '#333'}}>Keyboard</IonLabel>
                </IonSegmentButton>
                <IonSegmentButton value="map">
                    <IonLabel style={{fontSize: '0.9em', fontWeight: 'bold', color: '#333'}}>Select on Map</IonLabel>
                </IonSegmentButton>
            </IonSegment>

            {inputMode === 'name' && (
                <>
                <IonInput
                    label="Source Location"
                    labelPlacement="floating"
                    value={startName}
                    onIonInput={(e) => setStartName(e.detail.value!)}
                    placeholder="e.g., College or Lat,Lon"
                    fill="solid"
                    style={{marginBottom: '15px', '--background': '#f8f8f8', '--color': '#333'} as React.CSSProperties}
                />
                <IonInput
                    label="Destination Location"
                    labelPlacement="floating"
                    value={endName}
                    onIonInput={(e) => setEndName(e.detail.value!)}
                    placeholder="e.g., Hospital or Lat,Lon"
                    fill="solid"
                    style={{marginBottom: '20px', '--background': '#f8f8f8', '--color': '#333'} as React.CSSProperties}
                />
                </>
            )}

            {inputMode === 'map' && (
                <div style={{textAlign: 'center', marginBottom: '20px', padding: '15px', background: '#f0f8ff', borderRadius: '10px', border: '1px solid #b3d9ff'}}>
                    <IonLabel color="medium" style={{fontSize: '0.9em', display: 'block', marginBottom: '8px', color: '#0056b3'}}>
                        {isDroppingPin
                            ? "Click the map to place an incident pin."
                            : "Selected Coordinates:"
                        }
                    </IonLabel>
                    <IonLabel style={{fontSize: '0.85em', color: '#333', lineHeight: '1.6'}}>
                        <span style={{fontWeight: 'bold', color: '#0056b3'}}>Source:</span> ({startLat.toFixed(4)}, {startLon.toFixed(4)})<br/>
                        <span style={{fontWeight: 'bold', color: '#0056b3'}}>Dest:</span> ({endLat.toFixed(4)}, {endLon.toFixed(4)})
                    </IonLabel>
                    {incidentLocation && (
                        <IonLabel style={{fontSize: '0.85em', color: '#333', lineHeight: '1.6', marginTop: '5px', display: 'block'}}>
                            <span style={{fontWeight: 'bold', color: '#dc3545'}}>Incident Pin:</span> ({incidentLocation[0].toFixed(4)}, {incidentLocation[1].toFixed(4)})
                        </IonLabel>
                    )}
                </div>
            )}

            <IonButton expand="full" onClick={() => fetchRoute(null)} disabled={loading} color="success" style={{marginBottom: '15px', '--border-radius': '8px'} as React.CSSProperties}>
                {loading ? <IonSpinner name="dots" /> : 'GET FASTEST ROUTE'}
            </IonButton>

            <IonButton expand="full" onClick={handleDropPinClick} color="danger" fill={isDroppingPin ? "solid" : "outline"} style={{'--border-radius': '8px', marginBottom: '5px'} as React.CSSProperties}>
                {isDroppingPin ? "Click Map to Place Pin..." : "Drop Incident Pin"}
            </IonButton>

            {(isDroppingPin || incidentLocation) && (
                <IonButton expand="full" onClick={reportIncident} color="danger" fill={'solid'} style={{'--border-radius': '8px', marginBottom: '15px', marginTop: '10px'} as React.CSSProperties}>
                    REPORT ACCIDENT/POTHOLE
                </IonButton>
            )}

            {error && (<IonLabel color="danger" style={{fontSize: '0.9em', display: 'block', margin: '5px 0'}}>Error: {error}</IonLabel>)}
            {reportMessage && (
                <IonLabel
                    color={reportMessage.startsWith('INCIDENT') ? 'success' : (reportMessage.startsWith('Report Failed') ? 'danger' : 'medium')}
                    style={{fontSize: '0.9em', display: 'block', margin: '5px 0', textAlign: 'center', fontWeight: 'bold'}}
                >
                    {reportMessage}
                </IonLabel>
            )}
          </div>
        )}

        {/* Active Route Panel */}
        {isRouteActive && (
          <div style={{
            position: 'absolute',
            bottom: '20px',
            left: '50%',
            transform: 'translateX(-50%)',
            width: 'calc(100% - 40px)',
            maxWidth: '350px',
            zIndex: 10,
            background: 'rgba(255, 255, 255, 0.98)',
            padding: '15px',
            borderRadius: '16px',
            boxShadow: '0 8px 25px rgba(0, 0, 0, 0.2)',
            border: '1px solid #e0e0e0',
          }}>

            <div
              onClick={() => fetchRoute(null)}
              style={{
                background: '#0056b3',
                color: 'white',
                padding: '12px',
                borderRadius: '8px',
                textAlign: 'center',
                marginBottom: '15px',
                cursor: 'pointer'
              }}
            >
                <IonLabel style={{fontSize: '1.2em', fontWeight: 'bold', color: 'white'}}>
                    {totalTime !== null && `~ ${Math.floor(totalTime / 60)} min ${Math.round(totalTime % 60)} sec`}
                    {totalDistance !== null && ` / ${(totalDistance / 1000).toFixed(2)} km`}
                </IonLabel>
                <p style={{fontSize: '0.8em', margin: '5px 0 0 0', opacity: 0.8}}>
                    Est. Travel Time / Distance (Tap to refresh)
                </p>
            </div>

            <IonButton expand="full" onClick={handleDropPinClick} color="danger" fill={isDroppingPin ? "solid" : "outline"} style={{'--border-radius': '8px', marginBottom: '5px'} as React.CSSProperties}>
                {isDroppingPin ? "Click Map to Place Pin..." : "Drop Incident Pin"}
            </IonButton>

            {(isDroppingPin || incidentLocation) && (
                <IonButton expand="full" onClick={reportIncident} color="danger" fill={'solid'} style={{'--border-radius': '8px', marginBottom: '10px', marginTop: '10px'} as React.CSSProperties}>
                    REPORT ACCIDENT/POTHOLE
                </IonButton>
            )}

            <IonButton expand="full" onClick={clearRoute} color="medium" fill="outline" style={{'--border-radius': '8px'} as React.CSSProperties}>
                Clear Route / New Route
            </IonButton>

            {error && (<IonLabel color="danger" style={{fontSize: '0.9em', display: 'block', margin: '5px 0'}}>Error: {error}</IonLabel>)}
            {reportMessage && (
                <IonLabel
                    color={reportMessage.startsWith('INCIDENT') ? 'success' : (reportMessage.startsWith('Report Failed') ? 'danger' : 'medium')}
                    style={{fontSize: '0.9em', display: 'block', margin: '5px 0', textAlign: 'center', fontWeight: 'bold'}}
                >
                    {reportMessage}
                </IonLabel>
            )}

          </div>
        )}

        {/* Map Guidance Bar */}
        {!isRouteActive && inputMode === 'map' && mapClickState !== 'done' && (
          <div style={{
            position: 'absolute',
            bottom: '20px',
            left: '50%',
            transform: 'translateX(-50%)',
            width: 'calc(100% - 40px)',
            maxWidth: '350px',
            zIndex: 10,
            background: '#333',
            color: 'white',
            padding: '15px',
            borderRadius: '16px',
            boxShadow: '0 8px 25px rgba(0, 0, 0, 0.2)',
            textAlign: 'center'
          }}>
            <IonLabel style={{fontSize: '1.1em', fontWeight: 'bold'}}>
                {isDroppingPin
                    ? "Click map to drop incident pin"
                    : (mapClickState === 'source' ? "Select Source on Map" : "Select Destination on Map")
                }
            </IonLabel>
          </div>
        )}

      </IonContent>
    </IonPage>
  );
};

export default Home;