import { NextRequest, NextResponse } from "next/server";
import { getTelemetry } from "@/lib/db";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const range = request.nextUrl.searchParams.get("range") || "24h";
  const metric = request.nextUrl.searchParams.get("metric") || undefined;

  const rows = getTelemetry(range, metric);

  // Group by metric for chart consumption
  const grouped: Record<string, Array<{ value: number; timestamp: string }>> = {};
  for (const row of rows) {
    if (!grouped[row.metric]) grouped[row.metric] = [];
    grouped[row.metric].push({ value: row.value, timestamp: row.timestamp });
  }

  return NextResponse.json({ range, metrics: grouped });
}
