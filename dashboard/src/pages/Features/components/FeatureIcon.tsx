/**
 * Feature icons — map a ``feature.json`` ``icon_name`` (a kebab-case lucide
 * name, e.g. ``"receipt"``) to its lucide component. Mirrors
 * ``knowledgeIconForName`` / ``iconForName``: a curated map keeps the bundle
 * from pulling in the whole lucide set, and unknown names fall back to
 * ``Layers`` so cards never render blank.
 */

import type { LucideIcon } from "lucide-react";
import {
  Banknote,
  BarChart3,
  BookOpen,
  Boxes,
  Briefcase,
  Building2,
  Calculator,
  Calendar,
  Camera,
  ClipboardList,
  Clock,
  Database,
  FileCheck,
  FileSpreadsheet,
  FileText,
  Globe,
  Handshake,
  Languages,
  Layers,
  LayoutGrid,
  Lightbulb,
  ListTodo,
  Mail,
  MessageSquareText,
  Package,
  Palette,
  PenTool,
  Presentation,
  Receipt,
  ScrollText,
  Search,
  ShieldCheck,
  ShoppingBag,
  Sparkles,
  Target,
  TrendingUp,
  Truck,
  Users,
  Wallet,
  Waypoints,
  Workflow,
  Wrench,
} from "lucide-react";

const iconMap: Record<string, LucideIcon> = {
  receipt: Receipt,
  "file-text": FileText,
  "file-check": FileCheck,
  "file-spreadsheet": FileSpreadsheet,
  "clipboard-list": ClipboardList,
  "scroll-text": ScrollText,
  calculator: Calculator,
  banknote: Banknote,
  wallet: Wallet,
  "bar-chart-3": BarChart3,
  "trending-up": TrendingUp,
  users: Users,
  "building-2": Building2,
  briefcase: Briefcase,
  handshake: Handshake,
  mail: Mail,
  calendar: Calendar,
  clock: Clock,
  database: Database,
  globe: Globe,
  languages: Languages,
  "pen-tool": PenTool,
  palette: Palette,
  presentation: Presentation,
  "shopping-bag": ShoppingBag,
  package: Package,
  "list-todo": ListTodo,
  "book-open": BookOpen,
  search: Search,
  "shield-check": ShieldCheck,
  truck: Truck,
  wrench: Wrench,
  camera: Camera,
  sparkles: Sparkles,
  lightbulb: Lightbulb,
  target: Target,
  "message-square-text": MessageSquareText,
  waypoints: Waypoints,
  "layout-grid": LayoutGrid,
  workflow: Workflow,
  boxes: Boxes,
  layers: Layers,
};

export function FeatureIcon({
  name,
  size = 22,
  className,
}: {
  name?: string | null;
  size?: number;
  className?: string;
}) {
  const Icon = (name ? iconMap[name] : undefined) ?? Layers;
  return <Icon size={size} className={className} strokeWidth={1.8} />;
}
