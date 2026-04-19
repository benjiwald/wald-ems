import { NextRequest, NextResponse } from "next/server";
import { getSessions, getDb } from "@/lib/db";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const limit = parseInt(request.nextUrl.searchParams.get("limit") || "50");
  const rows = getSessions(limit);
  return NextResponse.json(rows);
}

export async function DELETE() {
  const db = getDb();
  const info = db.prepare("DELETE FROM charging_sessions").run();
  return NextResponse.json({ ok: true, deleted: info.changes });
}
