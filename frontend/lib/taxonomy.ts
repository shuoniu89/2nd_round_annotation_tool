import type { Code } from "@/lib/api";

// Codebook structure: research question → theme (broad code) → subthemes (detailed codes).
// Labels match the workbook headers after the "RQn:" / "RQn_GPT:" prefix; RQ2 detailed headers
// also carry a "Theme: " prefix, which is ignored when matching.
const STRUCTURE: Record<string, [theme: string, subthemes: string[]][]> = {
  RQ1: [
    ["Coping With Relational Adversity", [
      "Using AI as a Safe Outlet for Relational Trauma",
      "Using AI to Cope With Relationship Loss",
    ]],
    ["Compensating for Human Support Gaps", [
      "Experiencing Limited Access to Meaningful Social Connection",
      "Seeking Immediate Support Through Constant AI Availability",
    ]],
    ["Seeking Desired Relational Experience", [
      "Seeking a Specific Desired Relationship",
      "Seeking a Preferred Interpersonal Interaction Style",
    ]],
    ["Seeking Interest-Driven Interaction", ["Using AI for Interest-Based Exploration"]],
    ["Pursuing Social-Emotional Development", ["Using AI for Self-Directed Social-Emotional Management"]],
  ],
  RQ2: [
    ["Attachment", [
      "Roleplaying Intimate Relationships With AI",
      "Enacting Unavailable Intimacy Through AI Simulation",
    ]],
    ["Reliable Alliance", [
      "Disclosing Personal Difficulties to AI",
      "Returning to AI for Always-Available Companionship",
      "Maintaining Relational Continuity Through AI Memory",
    ]],
    ["Social Integration", ["Co-Creating Imaginative Roleplay With AI"]],
    ["Guidance", [
      "Following AI Suggestions for Behavior Change",
      "Practicing Social Interaction With AI Characters",
    ]],
    ["Reassurance of Worth", [
      "Reflecting on Personal Thoughts Through AI Conversation",
      "Receiving Validation Through AI Responses",
    ]],
  ],
  RQ3: [
    ["Relational Fulfillment", [
      "Experiencing Understanding Through Nonjudgmental AI Support",
      "Experiencing Perceived Reciprocity in an AI Relationship",
      "Preferring AI Relationships Over Real-World Relationships",
    ]],
    ["Real-World Behavior Improvement", ["Experiencing Real-World Behavior Change After AI Interaction"]],
    ["Relational Displacement", [
      "Developing Dependence Through Compulsive AI Use",
      "Experiencing Greater Loneliness After AI Interaction",
      "Replacing Professional Support With AI",
    ]],
    ["Relational Breakdown", [
      "Experiencing Relational Loss After Chatbot Dysfunction",
      "Experiencing Relational Rupture From Conversational Inconsistency",
      "Experiencing Relational Disruption From Content Moderation",
      "Experiencing Relational Breakdowns From AI Indifference",
    ]],
  ],
};

export const RQ_TITLES: Record<string, string> = {
  RQ1: "RQ1: Motivations",
  RQ2: "RQ2: Social Provisions",
  RQ3: "RQ3: Perceived Belonging Effects",
};

export type ThemeNode = { theme: Code | null; title: string; subthemes: Code[] };
export type GroupNode = { group: string; title: string; themes: ThemeNode[] };
export type Taxonomy = {
  groups: GroupNode[];
  parentOf: Map<string, string>;     // subtheme key → theme key
  childrenOf: Map<string, string[]>; // theme key → subtheme keys
  order: Map<string, number>;        // code key → display position
  shortLabel: (code: Code) => string;
  fullLabel: (code: Code) => string;
};

const normalize = (text: string) => text.toLowerCase().replace(/\s+/g, " ").trim();
/** Subtheme name without an RQ2-style "Theme: " prefix. */
const subthemeName = (code: Code) => code.kind === "detailed" && code.label.includes(": ")
  ? code.label.slice(code.label.indexOf(": ") + 2) : code.label;

export function buildTaxonomy(codes: Code[]): Taxonomy {
  const broad = new Map(codes.filter(c => c.kind === "broad").map(c => [`${c.group}|${normalize(c.label)}`, c]));
  const detailed = new Map(codes.filter(c => c.kind === "detailed").map(c => [`${c.group}|${normalize(subthemeName(c))}`, c]));
  const used = new Set<string>();
  const parentOf = new Map<string, string>();
  const childrenOf = new Map<string, string[]>();
  const groups: GroupNode[] = [];

  for (const group of [...new Set([...Object.keys(STRUCTURE), ...codes.map(c => c.group)])]) {
    const themes: ThemeNode[] = [];
    for (const [title, subtitles] of STRUCTURE[group] ?? []) {
      const theme = broad.get(`${group}|${normalize(title)}`) ?? null;
      const subthemes = subtitles.map(s => detailed.get(`${group}|${normalize(s)}`)).filter((c): c is Code => Boolean(c));
      if (!theme && subthemes.length === 0) continue;
      themes.push({ theme, title: theme?.label ?? title, subthemes });
    }
    themes.forEach(node => [node.theme, ...node.subthemes].forEach(c => c && used.add(c.key)));
    // Codes in the workbook that the structure above does not list are still offered:
    // remaining broad codes become themes without subthemes, others are listed together.
    const leftovers = codes.filter(c => c.group === group && !used.has(c.key));
    leftovers.filter(c => c.kind === "broad").forEach(c => { themes.push({ theme: c, title: c.label, subthemes: [] }); used.add(c.key); });
    const otherDetailed = leftovers.filter(c => c.kind !== "broad");
    if (otherDetailed.length) themes.push({ theme: null, title: "Other codes", subthemes: otherDetailed });
    otherDetailed.forEach(c => used.add(c.key));
    if (themes.length) groups.push({ group, title: RQ_TITLES[group] ?? group, themes });
  }

  const order = new Map<string, number>();
  for (const { themes } of groups) for (const node of themes) {
    if (node.theme) {
      order.set(node.theme.key, order.size);
      childrenOf.set(node.theme.key, node.subthemes.map(c => c.key));
      node.subthemes.forEach(c => parentOf.set(c.key, node.theme!.key));
    }
    node.subthemes.forEach(c => order.set(c.key, order.size));
  }

  const titleOf = new Map(codes.map(c => [c.key, c.label]));
  return {
    groups, parentOf, childrenOf, order,
    shortLabel: code => code.kind === "broad" ? code.label : subthemeName(code),
    fullLabel: code => {
      const parent = parentOf.get(code.key);
      return parent ? `${titleOf.get(parent)} › ${subthemeName(code)}` : code.label;
    },
  };
}
