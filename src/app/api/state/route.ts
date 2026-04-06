import { NextResponse } from "next/server";
import { getState } from "@/lib/db";

export const dynamic = "force-dynamic";

export async function GET() {
  const siteState = getState("site_state");
  if (!siteState) {
    return NextResponse.json({ status: "waiting", message: "EMS client not running" }, { status: 503 });
  }
  return NextResponse.json(siteState);
}
