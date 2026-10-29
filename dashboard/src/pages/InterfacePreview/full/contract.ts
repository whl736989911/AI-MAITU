import type { ReactNode } from "react";
import type { PreviewVariantProps } from "../model";

export type DemoScalar = string | number | boolean;
export type DemoValues = Record<string, DemoScalar>;
export interface DemoRow {
  id: string;
  name: string;
  description?: string;
  enabled?: boolean;
  values?: DemoValues;
}
export interface DemoField {
  name: string;
  label: string;
  kind?:
    | "text"
    | "textarea"
    | "number"
    | "switch"
    | "select"
    | "password"
    | "date";
  required?: boolean;
  options?: { label: string; value: string | number }[];
  initialValue?: DemoScalar;
  help?: string;
}
export interface DemoPageDefinition {
  path: string;
  titleKey: string;
  tabs?: readonly string[];
  tabItems?: Readonly<Record<string, string>>;
}
export interface DemoPageProps extends PreviewVariantProps {
  path: string;
  tab?: string;
  item?: string;
  navigate: (path: string, tab?: string, item?: string) => void;
}
export interface DemoCollectionProps {
  storageKey: string;
  title?: string;
  description?: string;
  initialRows: DemoRow[];
  fields: DemoField[];
  canCreate?: boolean;
  canEdit?: boolean;
  canDelete?: boolean;
  canToggle?: boolean;
  extra?: ReactNode;
  rowActions?: {
    key: string;
    label: string;
    onClick: (row: DemoRow) => void;
  }[];
  rowFilter?: (row: DemoRow) => boolean;
  onSaved?: (row: DemoRow, previous?: DemoRow) => void;
  onDeleted?: (row: DemoRow) => void;
}
export interface DemoSettingsProps {
  storageKey: string;
  title?: string;
  description?: string;
  fields: DemoField[];
  initialValues?: DemoValues;
  onSaved?: (values: DemoValues) => void;
}
