"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Box, Search } from "lucide-react";

export default function SiteHeader() {
  const pathname = usePathname();
  return (
    <header className="site-header">
      <nav className="site-nav" aria-label="主导航">
        <Link href="/" className="brand" aria-label="Car2LEGO_3D 首页"><Box size={20} strokeWidth={1.6} /><span>Car2LEGO_3D</span></Link>
        <div className="nav-links">
          <Link href="/" aria-current={pathname === "/" ? "page" : undefined}>创作</Link>
          <Link href="/sets" aria-current={pathname === "/sets" ? "page" : undefined}>车型</Link>
        </div>
        <Link href="/sets" className="icon-button" title="搜索车型" aria-label="搜索车型"><Search size={18} strokeWidth={1.6} /></Link>
      </nav>
    </header>
  );
}
