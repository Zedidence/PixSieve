// =============================================================
// PixSieve — Filter/Sort Web Worker
// Offloads filtering and sorting of duplicate groups off the
// main UI thread so large result sets stay responsive.
// =============================================================

self.onmessage = function (e) {
    try {
        const { groups, typeFilter, sortBy, search, generation } = e.data;

        if (!Array.isArray(groups)) {
            self.postMessage({ error: 'groups must be an array' });
            return;
        }

        let filtered = groups.filter(g => {
            if (typeFilter !== 'all' && g.match_type !== typeFilter) return false;
            if (search) {
                const hasMatch = g.images.some(img =>
                    img.filename.toLowerCase().includes(search) ||
                    img.directory.toLowerCase().includes(search)
                );
                if (!hasMatch) return false;
            }
            return true;
        });

        filtered.sort((a, b) => {
            if (sortBy === 'savings') return b.potential_savings - a.potential_savings;
            if (sortBy === 'count')   return b.image_count - a.image_count;
            return a.id - b.id;
        });

        self.postMessage({ filteredGroups: filtered, generation });
    } catch (err) {
        self.postMessage({ error: err.message || 'Filter worker error' });
    }
};
