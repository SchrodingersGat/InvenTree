import { t } from '@lingui/macro';
import { notifications } from '@mantine/notifications';
import { IconPrinter, IconReport, IconTags } from '@tabler/icons-react';
import { useCallback, useEffect, useMemo, useState } from 'react';

import { api } from '../../App';
import { ApiEndpoints } from '../../enums/ApiEndpoints';
import { ModelType } from '../../enums/ModelType';
import { extractAvailableFields } from '../../functions/forms';
import { useCreateApiFormModal } from '../../hooks/UseForm';
import { apiUrl } from '../../states/ApiState';
import { useLocalState } from '../../states/LocalState';
import { ApiFormFieldSet } from '../forms/fields/ApiFormField';
import { ActionDropdown } from '../items/ActionDropdown';

export function PrintingActions({
  items,
  enableLabels,
  enableReports,
  modelType
}: {
  items: number[];
  enableLabels?: boolean;
  enableReports?: boolean;
  modelType?: ModelType;
}) {
  const { host } = useLocalState.getState();

  const enabled = useMemo(() => items.length > 0, [items]);

  if (!modelType) {
    return null;
  }

  if (!enableLabels && !enableReports) {
    return null;
  }

  const [pluginId, setPluginId] = useState<number>(0);

  const loadFields = useCallback(() => {
    api
      .options(apiUrl(ApiEndpoints.label_print), {
        params: {
          plugin: pluginId
        }
      })
      .then((response: any) => {
        setExtraFields(extractAvailableFields(response, 'POST') || {});
      })
      .catch(() => {});
  }, [pluginId]);

  useEffect(() => {
    loadFields();
  }, [loadFields, pluginId]);

  const [extraFields, setExtraFields] = useState<ApiFormFieldSet>({});

  const labelFields: ApiFormFieldSet = useMemo(() => {
    let fields: ApiFormFieldSet = extraFields;

    // Override field values
    fields['template'] = {
      ...fields['template'],
      filters: {
        enabled: true,
        model_type: modelType,
        items: items.join(',')
      }
    };

    fields['items'] = {
      ...fields['items'],
      value: items,
      hidden: true
    };

    fields['plugin'] = {
      ...fields['plugin'],
      filters: {
        active: true,
        mixin: 'labels'
      },
      onValueChange: (value: string, record?: any) => {
        if (record?.pk && record?.pk != pluginId) {
          setPluginId(record.pk);
        }
      }
    };

    return fields;
  }, [extraFields, items, loadFields]);

  const labelModal = useCreateApiFormModal({
    url: apiUrl(ApiEndpoints.label_print),
    title: t`Print Label`,
    fields: labelFields,
    timeout: (items.length + 1) * 1000,
    onClose: () => {
      setPluginId(0);
    },
    onFormSuccess: (response: any) => {
      if (!response.complete) {
        // TODO: Periodically check for completion (requires server-side changes)
        notifications.show({
          title: t`Error`,
          message: t`The label could not be generated`,
          color: 'red'
        });
        return;
      }

      notifications.show({
        title: t`Success`,
        message: t`Label printing completed successfully`,
        color: 'green'
      });

      if (response.output) {
        // An output file was generated
        const url = `${host}${response.output}`;
        window.open(url, '_blank');
      }
    }
  });

  const reportModal = useCreateApiFormModal({
    title: t`Print Report`,
    url: apiUrl(ApiEndpoints.report_print),
    timeout: (items.length + 1) * 1000,
    fields: {
      template: {
        filters: {
          enabled: true,
          model_type: modelType,
          items: items.join(',')
        }
      },
      items: {
        hidden: true,
        value: items
      }
    },
    onFormSuccess: (response: any) => {
      if (!response.complete) {
        // TODO: Periodically check for completion (requires server-side changes)
        notifications.show({
          title: t`Error`,
          message: t`The report could not be generated`,
          color: 'red'
        });
        return;
      }

      notifications.show({
        title: t`Success`,
        message: t`Report printing completed successfully`,
        color: 'green'
      });

      if (response.output) {
        // An output file was generated
        const url = `${host}${response.output}`;
        window.open(url, '_blank');
      }
    }
  });

  return (
    <>
      {reportModal.modal}
      {labelModal.modal}
      <ActionDropdown
        tooltip={t`Printing Actions`}
        icon={<IconPrinter />}
        disabled={!enabled}
        actions={[
          {
            name: t`Print Labels`,
            icon: <IconTags />,
            onClick: () => labelModal.open(),
            hidden: !enableLabels
          },
          {
            name: t`Print Reports`,
            icon: <IconReport />,
            onClick: () => reportModal.open(),
            hidden: !enableReports
          }
        ]}
      />
    </>
  );
}