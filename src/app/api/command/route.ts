import { NextRequest, NextResponse } from "next/server";
import { createCommand } from "@/lib/db";

export async function POST(request: NextRequest) {
  const body = await request.json();
  const { action, ...payload } = body;

  if (!action) {
    return NextResponse.json({ error: "action required" }, { status: 400 });
  }

  const cmd = createCommand(action, payload);
  return NextResponse.json(cmd, { status: 201 });
}
