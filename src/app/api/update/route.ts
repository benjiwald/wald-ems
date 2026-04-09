import { NextResponse } from "next/server";
import { getState } from "@/lib/db";
import { execSync, spawn } from "child_process";
import path from "path";

export const dynamic = "force-dynamic";

/**
 * GET /api/update — Prueft auf neue Version (git fetch + compare)
 * POST /api/update — Fuehrt Update aus (git pull + rebuild + restart)
 */

function getInstallDir(): string {
  // Production: /opt/ems, Dev: project root
  if (process.env.NODE_ENV === "production") return "/opt/ems";
  return process.cwd();
}

export async function GET() {
  const installDir = getInstallDir();

  try {
    // safe.directory setzen (noetig wenn ems-User auf root-owned Repo zugreift)
    execSync(`git config --global --add safe.directory ${installDir}`, { timeout: 5000 }).toString();

    // Git fetch um Remote-Status zu holen
    execSync("git fetch origin main --quiet", { cwd: installDir, timeout: 15000 });

    const localHash = execSync("git rev-parse HEAD", { cwd: installDir }).toString().trim();
    const remoteHash = execSync("git rev-parse origin/main", { cwd: installDir }).toString().trim();
    const localDate = execSync("git log -1 --format=%ci HEAD", { cwd: installDir }).toString().trim();
    const behindCount = execSync("git rev-list HEAD..origin/main --count", { cwd: installDir }).toString().trim();

    // Aktuelle Version aus State-Tabelle
    const clientStatus = getState("client_status") as { version?: string } | null;

    return NextResponse.json({
      current_commit: localHash.substring(0, 7),
      remote_commit: remoteHash.substring(0, 7),
      current_date: localDate,
      behind: parseInt(behindCount),
      update_available: localHash !== remoteHash,
      client_version: clientStatus?.version || "unknown",
    });
  } catch (e) {
    return NextResponse.json({
      update_available: false,
      error: `Git-Check fehlgeschlagen: ${e instanceof Error ? e.message : e}`,
    });
  }
}

export async function POST() {
  const installDir = getInstallDir();
  const updateScript = path.join(installDir, "scripts", "update.sh");

  try {
    // Update-Skript im Hintergrund starten (non-blocking)
    // Das Skript restartet die Services am Ende selbst
    const child = spawn("sudo", [updateScript], {
      cwd: installDir,
      detached: true,
      stdio: "ignore",
    });
    child.unref();

    return NextResponse.json({
      ok: true,
      message: "Update gestartet. Services werden neu gestartet — Seite laedt in 30s automatisch neu.",
    });
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: `Update fehlgeschlagen: ${e instanceof Error ? e.message : e}` },
      { status: 500 }
    );
  }
}
