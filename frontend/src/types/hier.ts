// Shape of /api/hierarchical-summaries (see webapp/server.py build_summary_reader_data).

export interface HierNode {
  id: string;
  kind: 'event' | 'episode' | 'part';
  label: string;
  title: string;
  summaryId?: string;
  children?: string[];
}

export interface HierSummary {
  id: string;
  nodeId: string;
  tier: string;
  title: string;
  sectionOrder: string[];
  sections: Record<string, string>;
  meta?: { arcId?: string; episodeName?: string; partName?: string };
}

export interface Hierarchy {
  roots: string[];
  nodes: Record<string, HierNode>;
  summaries: Record<string, HierSummary>;
  counts: { events: number; episodes: number; parts: number };
}

// arc_slug from an "event:<arc>" node id.
export const arcOf = (nodeId: string): string => nodeId.replace(/^event:/, '');
