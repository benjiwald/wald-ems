import { NextRequest, NextResponse } from "next/server";
import { getSessions } from "@/lib/db";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const limit = parseInt(request.nextUrl.searchParams.get("limit") || "50");
  const rows = getSessions(limit);
  return NextResponse.json(rows);
}
