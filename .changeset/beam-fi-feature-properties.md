---
"@platforma-open/milaboratories.feature-integration": patch
"@platforma-open/milaboratories.feature-integration.workflow": patch
"@platforma-open/milaboratories.feature-integration.per-cell-metrics": patch
---

Import per-feature properties from the tag→feature CSV's extra columns (A-0026).

Every column of the tag→feature CSV beyond the mapped barcode-sequence and feature-name columns is now
imported as a per-feature property and attached to the shared feature axis (`pl7.app/feature/featureId`)
— one String p-column per column (name `pl7.app/feature/property`, the raw header carried in the domain
`pl7.app/feature/propertyName`, distinct values in `discreteValues`). The pass-through is generic, with
no hardcoded schema, so arbitrary properties (antigen type, species, pool, …) flow through. Published as
a separate `featureProperties` export frame so the properties ride the feature axis into VDJ Multiomic
Integration's per-feature outputs and lead selection with no re-import; downstream can group / filter the
binding profile by property (e.g. all human vs all cyno antigens).
