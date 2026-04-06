import { NextRequest, NextResponse } from "next/server";
import { getLogs } from "@/lib/db";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const limit = parseInt(request.nextUrl.searchParams.get("limit") || "100");
  const level = request.nextUrl.searchParams.get("level") || undefined;
  const rows = getLogs(limit, level);
  return NextResponse.json(rows);
}
