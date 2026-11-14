import { createFileRoute } from "@tanstack/react-router";
import { PrototypeApp } from "@/components/pyshield/app";

export const Route = createFileRoute("/")({ component: Home });

function Home() {
  return <PrototypeApp />;
}
