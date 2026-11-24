"use client";
import { useState } from "react";
import type { Code } from "@/lib/api";
import type { Taxonomy, ThemeNode } from "@/lib/taxonomy";

/**
 * Cascading code selection: choose a theme, then any of its subthemes.
 * - Checking a theme reveals its subthemes; unchecking it also clears them.
 * - Checking a subtheme also checks its theme, so a subtheme never stands alone.
 * - "All" / "None" select or clear every subtheme of a theme in one step.
 * - "Show subthemes" previews a theme's subthemes without selecting it.
 * - While searching, matching subthemes are shown even if their theme is unchecked.
 */
export function CodeTree({ taxonomy, selected, disabled, search, onChange }: {
  taxonomy: Taxonomy; selected: Set<string>; disabled: boolean; search: string;
  onChange: (next: string[]) => void;
}) {
  const [previewing, setPreviewing] = useState<Set<string>>(new Set());
  const query = search.trim().toLowerCase();
  const matches = (code: Code) => `${taxonomy.shortLabel(code)} ${code.header} ${code.key}`.toLowerCase().includes(query);

  function commit(add: string[], remove: string[] = []) {
    const next = new Set(selected);
    remove.forEach(key => next.delete(key));
    add.forEach(key => next.add(key));
    onChange([...next]);
  }
  function toggleTheme(theme: Code) {
    const children = taxonomy.childrenOf.get(theme.key) ?? [];
    if (selected.has(theme.key)) commit([], [theme.key, ...children]);
    else commit([theme.key]);
  }
  function toggleSubtheme(code: Code) {
    const parent = taxonomy.parentOf.get(code.key);
    if (selected.has(code.key)) commit([], [code.key]);
    else commit(parent ? [code.key, parent] : [code.key]);
  }
  function togglePreview(key: string) {
    setPreviewing(current => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }

  function renderTheme(node: ThemeNode) {
    const { theme } = node;
    const themeMatches = theme ? matches(theme) : false;
    const visibleSubs = query && !themeMatches ? node.subthemes.filter(matches) : node.subthemes;
    if (query && !themeMatches && visibleSubs.length === 0) return null;
    const checked = theme ? selected.has(theme.key) : false;
    const open = !theme || checked || Boolean(query) || previewing.has(theme.key);
    const chosen = node.subthemes.filter(c => selected.has(c.key)).length;
    const key = theme?.key ?? `other-${node.title}`;

    return <div key={key} className={`theme${checked ? " theme-selected" : ""}`}>
      {theme ? <div className="theme-row">
        <label className="code-choice theme-choice">
          <input type="checkbox" aria-label={theme.header} checked={checked} disabled={disabled} onChange={() => toggleTheme(theme)} />
          <span>{theme.label}</span>
        </label>
        {node.subthemes.length > 0 && <span className="theme-tools">
          {checked && <span className="theme-count">{chosen}/{node.subthemes.length}</span>}
          {checked && <button type="button" className="mini" disabled={disabled || chosen === node.subthemes.length}
            aria-label={`Select all subthemes of ${theme.label}`}
            onClick={() => commit([theme.key, ...node.subthemes.map(c => c.key)])}>All</button>}
          {checked && <button type="button" className="mini" disabled={disabled || chosen === 0}
            aria-label={`Clear subthemes of ${theme.label}`}
            onClick={() => commit([], node.subthemes.map(c => c.key))}>None</button>}
          {!checked && !query && <button type="button" className="link-button mini-link" aria-expanded={open}
            onClick={() => togglePreview(theme.key)}>{open ? "Hide subthemes" : "Show subthemes"}</button>}
        </span>}
      </div> : <p className="theme-row other-title">{node.title}</p>}
      {open && visibleSubs.length > 0 && <div className="subtheme-list" role="group" aria-label={theme ? `Subthemes of ${theme.label}` : node.title}>
        {checked && chosen === 0 && <p className="subtheme-warning">⚠ No subtheme selected. Choose at least one subtheme for this theme.</p>}
        {visibleSubs.map(code => <label key={code.key} className="code-choice subtheme-choice">
          <input type="checkbox" aria-label={code.header} checked={selected.has(code.key)} disabled={disabled} onChange={() => toggleSubtheme(code)} />
          <span>{taxonomy.shortLabel(code)}</span>
        </label>)}
      </div>}
    </div>;
  }

  const rendered = taxonomy.groups.map(group => {
    const themes = group.themes.map(renderTheme).filter(Boolean);
    return themes.length > 0 && <fieldset key={group.group}>
      <legend>{group.title}</legend>
      {themes}
    </fieldset>;
  }).filter(Boolean);

  return <>
    {rendered}
    {rendered.length === 0 && <p className="help-text">No codes match your search.</p>}
  </>;
}
