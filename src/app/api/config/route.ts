import { NextRequest, NextResponse } from "next/server";
import { getConfig, getConfigPath } from "@/lib/config";
import fs from "fs";
import yaml from "js-yaml";

export const dynamic = "force-dynamic";

export async function GET() {
  const config = getConfig();
  return NextResponse.json(config);
}

export async function PUT(request: NextRequest) {
  const body = await request.json();
  const configPath = getConfigPath();

  // Validate: must be a valid object
  if (!body || typeof body !== "object") {
    return NextResponse.json({ error: "invalid config" }, { status: 400 });
  }

  const yamlStr = yaml.dump(body, { indent: 2, lineWidth: 120 });
  fs.writeFileSync(configPath, yamlStr, "utf-8");

  return NextResponse.json({ ok: true, path: configPath });
}
