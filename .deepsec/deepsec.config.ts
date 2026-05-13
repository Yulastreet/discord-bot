import { defineConfig } from "deepsec/config";

export default defineConfig({
  projects: [
    { id: "discord-bot", root: ".." },
    // <deepsec:projects-insert-above>
  ],
});
