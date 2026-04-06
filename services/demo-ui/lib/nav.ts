import { LayoutDashboard, PlayCircle, ServerCog, Siren } from "lucide-react";

export const navItems = [
  {
    href: "/",
    label: "Overview",
    description: "High-level metrics and trends",
    icon: LayoutDashboard,
  },
  {
    href: "/incidents",
    label: "Incidents",
    description: "Manage incident workflow and status",
    icon: Siren,
  },
  {
    href: "/services",
    label: "Services",
    description: "Health, integrations, and links",
    icon: ServerCog,
  },
  {
    href: "/demo",
    label: "Demo Scenarios",
    description: "Run scenarios on demand",
    icon: PlayCircle,
  },
] as const;
