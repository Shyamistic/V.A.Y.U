import { z } from 'zod';
import type { Workspace } from './types';

const numericSeries = z.array(z.number().finite()).length(96);
const findingSchema = z.object({ code: z.string(), severity: z.string(), constraint: z.string(), measured_value: z.union([z.number().finite(), z.string()]).nullable(), limit_value: z.union([z.number().finite(), z.string()]).nullable(), unit: z.string().nullable() }).passthrough().transform(finding => ({ ...finding, message: `${finding.constraint}; observed ${finding.measured_value ?? 'unavailable'}, limit ${finding.limit_value ?? 'unavailable'} ${finding.unit ?? ''}` }));
const slotSchema = z.object({ slot_index: z.number().int().min(0).max(95), timestamp_utc: z.string(), renewable_available_mw: z.number().finite(), demand_mw: z.number().finite(), bess_energy_mwh: z.record(z.number().finite()), bess_soc_fraction: z.record(z.number().finite()), action_applied: z.boolean() }).passthrough();
const schema = z.object({
  portfolio: z.object({ assets: z.array(z.object({ asset_id: z.string(), kind: z.string() }).passthrough()) }).passthrough(),
  replay: z.object({ replay_id: z.string(), realized_path_id: z.string(), slot_states: z.array(slotSchema).length(96), decisions: z.array(z.object({ slot_index: z.number().int(), status: z.string(), violations: z.array(findingSchema), recovery_options: z.array(z.string()) }).passthrough()), scenario_set: z.object({ paths: z.array(z.object({ scenario_id: z.string(), renewable_available_mw: z.record(numericSeries), demand_mw: z.record(numericSeries), price_usd_per_mwh: numericSeries }).passthrough()) }).passthrough() }).passthrough(),
  metrics: z.object({ values: z.array(z.object({ name: z.string(), value: z.number().finite().nullable(), status: z.string(), unit: z.string().nullable(), provenance: z.object({ label: z.string() }).passthrough() }).passthrough()) }).passthrough(),
  summary: z.object({ policy_mode: z.string() }).passthrough(),
  certificate: z.object({ certificate_id: z.string(), unsigned_digest: z.string() }).passthrough(),
  asset_series: z.object({ generation_mw: z.record(numericSeries), demand_mw: z.record(numericSeries), bess_available: z.record(z.array(z.boolean()).length(96)), tie_capacity_mw: numericSeries }),
  forecasts: z.array(z.object({ forecast_id: z.string(), p10: numericSeries, p50: numericSeries, p90: numericSeries }).passthrough()),
  events: z.array(z.object({ event_id: z.string(), start_slot: z.number(), end_slot: z.number() }).passthrough()),
  science: z.object({ status: z.string(), reports: z.array(z.object({ experiment: z.string(), result: z.record(z.unknown()) }).passthrough()), issues: z.array(z.string()) }),
  obligations: z.array(z.object({ obligation_id: z.string() }).passthrough()),
  hardware: z.object({ mode: z.string(), connection: z.string(), cases: z.array(z.object({ case_id: z.string(), frame: z.record(z.unknown()) })), config: z.record(z.number()) }),
  scope: z.string(), layout: z.string(), forecast_integration: z.string(), limitations: z.array(z.string()),
}).passthrough();

export function parseWorkspace(value: unknown): Workspace {
  const parsed = schema.safeParse(value);
  if (!parsed.success) throw new Error(`Workspace contract rejected at ${parsed.error.issues[0]?.path.join('.') || 'root'}. Replay evidence must be refreshed together.`);
  if (!parsed.data.replay.scenario_set.paths.some(path => path.scenario_id === parsed.data.replay.realized_path_id)) throw new Error('The realized scenario is missing from replay evidence.');
  if (parsed.data.replay.slot_states.some((slot, index) => slot.slot_index !== index)) throw new Error('Replay slots are incomplete or out of order.');
  return parsed.data as unknown as Workspace;
}