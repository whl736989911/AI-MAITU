import type { ReactNode } from "react";
import { Search } from "lucide-react";
import { useFilteredList } from "../../hooks/useFilteredList";
import styles from "./picker.module.less";

export type PickerPanelWidth = "wide" | "narrow" | "compact";

interface SearchablePickerPanelProps<T> {
  items: T[];
  filterFn: (item: T, query: string) => boolean;
  searchPlaceholder: string;
  emptyMessage: string;
  width?: PickerPanelWidth;
  renderItem: (item: T) => ReactNode;
  /**
   * Heading to draw above an item, or ``null`` for the default group. A panel
   * that lists one group passes nothing and renders exactly as before; a panel
   * whose list holds a second group labels that group, and the heading is drawn
   * where the group starts so it stays above the rows it names however the
   * search narrows them.
   */
  groupLabelFor?: (item: T) => string | null;
  footerIcon: ReactNode;
  footerLabel: string;
  onFooterClick: () => void;
}

export default function SearchablePickerPanel<T>({
  items,
  filterFn,
  searchPlaceholder,
  emptyMessage,
  width = "wide",
  renderItem,
  groupLabelFor,
  footerIcon,
  footerLabel,
  onFooterClick,
}: SearchablePickerPanelProps<T>) {
  const { query, setQuery, filtered } = useFilteredList(items, filterFn);
  const panelClass =
    width === "compact"
      ? styles.panelCompact
      : width === "narrow"
      ? styles.panelNarrow
      : styles.panelWide;

  return (
    <div className={panelClass}>
      <div className={styles.search}>
        <input
          type="search"
          className={styles.searchInput}
          placeholder={searchPlaceholder}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <Search size={15} className={styles.searchIcon} aria-hidden />
      </div>

      <div className={styles.list}>
        {filtered.length === 0 ? (
          <div className={styles.empty}>{emptyMessage}</div>
        ) : (
          filtered.map((item, index) => {
            const label = groupLabelFor?.(item) ?? null;
            const previous =
              index > 0 ? groupLabelFor?.(filtered[index - 1]) ?? null : null;
            if (label === null || label === previous) return renderItem(item);
            return [
              <div key={`group:${index}`} className={styles.groupLabel}>
                {label}
              </div>,
              renderItem(item),
            ];
          })
        )}
      </div>

      <button type="button" className={styles.footer} onClick={onFooterClick}>
        {footerIcon}
        <span>{footerLabel}</span>
      </button>
    </div>
  );
}

export { styles as pickerStyles };
