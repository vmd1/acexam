export interface HierarchicalTopic {
  id: string;
  parent_id: string | null;
}

export interface TopicGroup<T> {
  label: string;
  topics: T[];
}

// Groups topics by their real specification hierarchy (parent_id), so the
// UI can show "Topic 1: Cell biology" -> its actual sub-topics, instead of
// guessing groups from a spec_code string prefix. Falls back gracefully
// when no hierarchy has been set (e.g. legacy flat data): every topic with
// no parent becomes its own top-level group.
export function groupByTopicHierarchy<T extends HierarchicalTopic & { title: string }>(
  topics: T[]
): TopicGroup<T>[] {
  const byId = new Map(topics.map(t => [t.id, t]));
  const childIds = new Set(topics.filter(t => t.parent_id).map(t => t.parent_id as string));

  const topLevel = topics.filter(t => !t.parent_id || !byId.has(t.parent_id));

  return topLevel.map((top, idx) => {
    const descendants: T[] = [];
    const queue = [top.id];
    const seen = new Set([top.id]);
    while (queue.length) {
      const current = queue.shift()!;
      topics.forEach(t => {
        if (t.parent_id === current && !seen.has(t.id)) {
          seen.add(t.id);
          descendants.push(t);
          queue.push(t.id);
        }
      });
    }

    let leafTopics = descendants.filter(t => !childIds.has(t.id));
    if (leafTopics.length === 0) leafTopics = [top];

    return { label: `Topic ${idx + 1}: ${top.title}`, topics: leafTopics };
  });
}
