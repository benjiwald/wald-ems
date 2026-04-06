"use client";

import { useState, useEffect } from "react";
import { Sun, Moon, Zap } from "lucide-react";

export default function Header() {
  const [theme, setTheme] = useState<"dark" | "light">("dark");

  useEffect(() => {
    const stored = localStorage.getItem("ems-theme") as "dark" | "light" | null;
    if (stored) setTheme(stored);
  }, []);

  function toggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    localStorage.setItem("ems-theme", next);
    document.documentElement.setAttribute("data-theme", next);
  }

  return (
    <header className="glass-header sticky top-0 z-50 px-6 py-3 flex items-center justify-between">
      <a href="/" className="flex items-center gap-3 hover:opacity-80 transition-opacity">
        <div className="w-8 h-8 rounded-lg bg-primary/20 flex items-center justify-center">
          <Zap className="w-5 h-5 text-primary" />
        </div>
        <h1 className="text-lg font-semibold tracking-tight">Wald EMS</h1>
      </a>
      <div className="flex items-center gap-4">
        <a href="/sessions" className="text-sm text-muted-foreground hover:text-foreground transition-colors">
          Ladevorgaenge
        </a>
        <a href="/settings" className="text-sm text-muted-foreground hover:text-foreground transition-colors">
          Einstellungen
        </a>
        <button onClick={toggleTheme} className="p-2 rounded-lg hover:bg-muted transition-colors">
          {theme === "dark" ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
        </button>
      </div>
    </header>
  );
}
