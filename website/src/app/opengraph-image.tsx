import { readFileSync } from "node:fs";
import { join } from "node:path";
import { ImageResponse } from "next/og";

export const alt = "Jarvis: a free, open-source voice assistant for Windows and Mac";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

const condensed = readFileSync(join(process.cwd(), "node_modules/@fontsource/barlow-condensed/files/barlow-condensed-latin-600-normal.woff"));
const mark = `data:image/png;base64,${readFileSync(join(process.cwd(), "public/brand/jarvis-mark.png")).toString("base64")}`;

export default function OpengraphImage() {
  const bracket = (pos: Record<string, number>) => ({
    position: "absolute" as const, width: 70, height: 70, ...pos,
    borderColor: "rgba(53,205,250,.55)", borderStyle: "solid" as const,
    borderTopWidth: pos.top !== undefined ? 2 : 0, borderBottomWidth: pos.bottom !== undefined ? 2 : 0,
    borderLeftWidth: pos.left !== undefined ? 2 : 0, borderRightWidth: pos.right !== undefined ? 2 : 0,
  });
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%", height: "100%", display: "flex", alignItems: "center", padding: "0 90px", gap: 70,
          background: "radial-gradient(ellipse 60% 80% at 75% 45%, rgba(20,172,223,.28), transparent 70%), linear-gradient(180deg, #041a2c, #03111e)",
          color: "#f5f5f8", fontFamily: "Barlow Condensed",
        }}
      >
        <div style={bracket({ top: 40, left: 40 })} />
        <div style={bracket({ top: 40, right: 40 })} />
        <div style={bracket({ bottom: 40, left: 40 })} />
        <div style={bracket({ bottom: 40, right: 40 })} />
        <div style={{ display: "flex", flexDirection: "column", flex: 1 }}>
          <div style={{ fontSize: 25, letterSpacing: 6, color: "#35cdfa", textTransform: "uppercase" }}>Free · open source · Windows and Mac</div>
          <div style={{ fontSize: 132, letterSpacing: 22, marginTop: 18, lineHeight: 1 }}>JARVIS</div>
          <div style={{ fontSize: 52, marginTop: 26, lineHeight: 1.1, color: "rgba(245,245,248,.86)" }}>
            The voice assistant that does things on your computer. Safely.
          </div>
        </div>
        {/* eslint-disable-next-line @next/next/no-img-element -- ImageResponse renders plain img */}
        <img src={mark} width={330} height={330} alt="" />
      </div>
    ),
    { ...size, fonts: [{ name: "Barlow Condensed", data: condensed, weight: 600, style: "normal" }] },
  );
}
