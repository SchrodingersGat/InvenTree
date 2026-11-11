import { t } from '@lingui/core/macro';
import { useMemo } from 'react';

import type { ApiFormFieldSet } from '@lib/types/Forms';

/**
 * Field set for creating or editing a StockItemCostEntry.
 *
 * All costs are *per unit* of the stock item.
 * The "source" field is not shown - entries created via the UI are recorded as manual entries.
 */
export function useStockItemCostEntryFields({
  stockItemId
}: {
  stockItemId: number;
}): ApiFormFieldSet {
  return useMemo(() => {
    return {
      stock_item: {
        value: stockItemId,
        hidden: true
      },
      cost_type: {},
      currency: {},
      min_cost: {
        label: t`Minimum Unit Cost`,
        description: t`Minimum cost per unit (leave blank to match the maximum cost)`
      },
      max_cost: {
        label: t`Maximum Unit Cost`,
        description: t`Maximum cost per unit (leave blank to match the minimum cost)`
      },
      estimated: {},
      notes: {}
    };
  }, [stockItemId]);
}
