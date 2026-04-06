import { NextResponse } from "next/server";
import fs from "fs";
import yaml from "js-yaml";
import { getConfigPath } from "@/lib/config";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const configPath = getConfigPath();

    // Check if config file exists
    if (!fs.existsSync(configPath)) {
      return NextResponse.json({ configured: false });
    }

    // Check if file has meaningful content (not just defaults)
    const raw = fs.readFileSync(configPath, "utf-8");
    const config = yaml.load(raw) as Record<string, unknown>;

    // Configured = has at least one meter defined
    const meters = Array.isArray(config?.meters) ? config.meters : [];
    return NextResponse.json({ configured: meters.length > 0 });
  } catch {
    return NextResponse.json({ configured: false });
  }
}
