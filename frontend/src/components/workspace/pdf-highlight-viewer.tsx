"use client";

import * as React from "react";

export interface Box {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

/**
 * Renders a server-rendered page image with citation bounding boxes overlaid.
 *
 * Boxes are normalised to [0,1] against the page, so percentage positioning
 * maps them exactly onto the image regardless of its rendered size — no PDF
 * engine runs in the browser (robust + offline-safe).
 */
export function PdfHighlightViewer({
  src,
  boxes,
  width = 640,
}: {
  src: string;
  boxes: Box[];
  width?: number;
}) {
  return (
    <div className="relative inline-block overflow-hidden rounded-sm border shadow-sm" style={{ width }}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={src} alt="Source document page" className="block w-full" />
      {boxes.map((b, i) => (
        <div
          key={i}
          className="pointer-events-none absolute rounded-[2px] border-2 border-warning bg-warning/25 ring-1 ring-warning/50"
          style={{
            left: `${Math.min(b.x0, b.x1) * 100}%`,
            top: `${Math.min(b.y0, b.y1) * 100}%`,
            width: `${Math.abs(b.x1 - b.x0) * 100}%`,
            height: `${Math.abs(b.y1 - b.y0) * 100}%`,
          }}
        />
      ))}
    </div>
  );
}
