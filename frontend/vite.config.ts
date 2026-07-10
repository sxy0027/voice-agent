import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

import { workbenchCodexPlugin } from "./workbenchCodexPlugin";

export default defineConfig({
  plugins: [react(), workbenchCodexPlugin()],
});
