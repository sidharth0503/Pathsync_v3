import { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.pathsync.v3',
  appName: 'Pathsync',
  webDir: 'dist',
  // --- ADD THIS BLOCK ---
  server: {
    androidScheme: 'http',
    cleartext: true
  }
  // ----------------------
};

export default config;