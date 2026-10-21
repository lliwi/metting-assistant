import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Metting Agent",
    short_name: "Metting",
    description: "Graba, transcribe y convierte tus reuniones en apuntes, actas y tareas.",
    start_url: "/",
    display: "standalone",
    background_color: "#f7f7f5",
    theme_color: "#2f5bd3",
    icons: [
      { src: "/icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any" },
      { src: "/icon-maskable.svg", sizes: "any", type: "image/svg+xml", purpose: "maskable" },
    ],
  };
}
