import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Wald EMS",
  description: "Lokales Energiemanagementsystem",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="de" data-theme="dark">
      <head>
        <script dangerouslySetInnerHTML={{ __html:
          `(function(){var t=localStorage.getItem('ems-theme');` +
          `if(t==='light')document.documentElement.setAttribute('data-theme','light');` +
          `else document.documentElement.setAttribute('data-theme','dark');})();`
        }} />
      </head>
      <body className="antialiased">{children}</body>
    </html>
  );
}
