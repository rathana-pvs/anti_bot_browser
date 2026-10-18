# Manager UI system

Application screens should use these primitives instead of repeating control,
focus, status, and panel styles. `Select` and `Checkbox` are the only supported
dropdown and checkbox controls; do not add native equivalents.

Semantic design tokens live in `src/styles/globals.css` and are exposed through
`tailwind.config.js`. Prefer semantic utilities such as `bg-surface`,
`text-muted`, `border-border`, and `bg-accent`. Zinc utilities remain available
for gradual migration, but new component APIs should not expose raw colors.

Core primitives:

- `Button` and `IconButton` for actions.
- `Input`, `Textarea`, `Select`, `Checkbox`, `RadioGroup`, and `NumberInput` for controls.
- `Field` for labels, descriptions, and validation messages.
- `Dialog`, `Card`, `Tabs`, `Badge`, `Alert`, `EmptyState`, `Spinner`, and `Toast` for structure and feedback.

Every icon-only action needs an accessible name. Use `loading` on `Button`,
`Alert` for actionable status, and `EmptyState` when a collection has no items.
Keep feature-specific layout in the feature component and visual states in the
primitive variant APIs.
