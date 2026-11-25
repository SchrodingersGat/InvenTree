import { t } from '@lingui/core/macro';
import { Text } from '@mantine/core';
import type { ReactNode } from 'react';
import type { NavigateFunction } from 'react-router-dom';

import { ModelType } from '@lib/enums/ModelType';
import { getDetailUrl } from '@lib/functions/Navigation';
import { RenderInlineModel } from './Instance';

/**
 * Detail of the (generic) object which has consumed a stock item,
 * as provided by the "consumed_by_detail" field of the StockItem API.
 */
export type ConsumedByDetail = {
  model_type: string;
  pk: number;
  name: string | null;
  url: string | null;
  deleted: boolean;
};

/**
 * Return a label describing the consumer of a stock item, based on the consumer type
 */
export function consumerLabel(modelType: string | null | undefined): string {
  switch (modelType) {
    case ModelType.stockitem:
      return t`Installed In`;
    case ModelType.build:
      return t`Consumed By`;
    case ModelType.salesorder:
      return t`Sales Order`;
    case ModelType.company:
      return t`Customer`;
    default:
      return t`Consumed By`;
  }
}

/**
 * Return a description of the consumption status of a stock item
 */
export function consumerDescription(
  modelType: string | null | undefined
): string {
  switch (modelType) {
    case ModelType.stockitem:
      return t`This stock item is installed in another stock item`;
    case ModelType.build:
      return t`This stock item has been consumed by a build order`;
    case ModelType.salesorder:
      return t`This stock item has been shipped against a sales order`;
    case ModelType.company:
      return t`This stock item has been assigned to a customer`;
    default:
      return t`This stock item has been consumed`;
  }
}

/**
 * Render a link to the object which has consumed a stock item.
 *
 * - If the consumer has been deleted, it is described by type and ID
 * - If the consumer detail is not available (e.g. the user cannot view it),
 *   it is described by type and ID only
 */
export function RenderConsumer({
  record,
  navigate
}: Readonly<{
  record: any;
  navigate?: NavigateFunction;
}>): ReactNode {
  const modelType: string | null = record?.consumed_by_model_type ?? null;
  const modelId: number | null = record?.consumed_by_model_id ?? null;
  const detail: ConsumedByDetail | null = record?.consumed_by_detail ?? null;

  if (!modelType) {
    return null;
  }

  if (detail?.deleted) {
    return (
      <Text size='sm' c='dimmed' fs='italic'>
        {t`Deleted`} {modelType} #{modelId}
      </Text>
    );
  }

  if (!detail) {
    return (
      <Text size='sm'>
        {modelType} #{modelId}
      </Text>
    );
  }

  return (
    <RenderInlineModel
      primary={detail.name ?? `${modelType} #${detail.pk}`}
      url={getDetailUrl(detail.model_type as ModelType, detail.pk)}
      navigate={navigate}
    />
  );
}

/**
 * Return a plain-text summary of the consumer of a stock item (e.g. for a tooltip)
 */
export function consumerSummary(record: any): string {
  const modelType: string | null = record?.consumed_by_model_type ?? null;

  if (!modelType) {
    return '';
  }

  const modelId: number | null = record?.consumed_by_model_id ?? null;
  const detail: ConsumedByDetail | null = record?.consumed_by_detail ?? null;

  let name: string;

  if (detail?.deleted) {
    name = `${t`Deleted`} ${modelType} #${modelId}`;
  } else if (detail?.name) {
    name = detail.name;
  } else {
    name = `${modelType} #${modelId}`;
  }

  return `${consumerDescription(modelType)}: ${name}`;
}
