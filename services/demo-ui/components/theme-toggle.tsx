"use client";

import * as React from "react";
import { MoonStar, SunMedium } from "lucide-react";
import { useTheme } from "next-themes";

import { Button } from "@/components/ui/button";

export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = React.useState(false);

  React.useEffect(() => {
    setMounted(true);
  }, []);

  const isDark = mounted ? resolvedTheme === "dark" : true;
  const nextTheme = isDark ? "light" : "dark";

  return (
    <Button
      type="button"
      variant="secondary"
      size="sm"
      className="min-w-[8.5rem] justify-center"
      aria-label={mounted ? `Switch to ${nextTheme} theme` : "Toggle color theme"}
      title={mounted ? `Switch to ${nextTheme} theme` : "Toggle color theme"}
      onClick={() => setTheme(nextTheme)}
    >
      {isDark ? <SunMedium className="mr-2 h-4 w-4" /> : <MoonStar className="mr-2 h-4 w-4" />}
      {isDark ? "Dark mode" : "Light mode"}
    </Button>
  );
}
