# Ruby pattern rulebooks

Read by the architect and the `design` judge for `*.rb`/`*.erb` changes. Each pattern names the trigger that calls for it, the form the practices rulebooks accept, and when to leave it alone; a cited `RB-n` rule is the authority, not restated here.

- **RB-P1 — Template Method.**
  pattern: Template Method — a fixed sequence with a few steps that vary per subtype.
  trigger: several classes repeat the same sequence and differ only in one or two steps.
  form: a base class with hook methods for the varying steps; a block or a Strategy is usually lighter.
  not when: the whole algorithm varies, or only one subclass will ever exist — pass a block instead (see RB-P14).

- **RB-P2 — Strategy.**
  pattern: Strategy — swap an algorithm at runtime.
  trigger: behaviour must vary by composition, and the choice arrives as data or config.
  form: a Proc, a lambda, or any duck that responds to `call`; a class only when the strategy holds state.
  not when: the variation is a single branch the caller already decided — a method argument is enough.

- **RB-P3 — Observer.**
  pattern: Observer — decouple side effects from the change that triggers them.
  trigger: one change must notify several interested parties that the code should not know by name.
  form: `ActiveSupport::Notifications` or another publish/subscribe seam; keep business logic out of Active Record callbacks.
  not when: there is one listener and it lives in the same object — call it directly.

- **RB-P4 — Composite.**
  pattern: Composite — trees where leaves and branches answer the same messages.
  trigger: a structure nests to arbitrary depth: threaded comments, nested forms, menus.
  form: one interface (or duck) implemented by both the leaf and the container, with recursion at the container.
  not when: the nesting is exactly one level — a plain association is clearer.

- **RB-P5 — Iterator.**
  pattern: Iterator — traverse a collection without exposing its structure.
  trigger: callers need to walk a collection the object owns, in an order the object defines.
  form: define `each` and `include Enumerable`; `Enumerator` and `.lazy` cover external and lazy iteration.
  not when: the collection is already an Array or a Relation the caller may take over.

- **RB-P6 — Command.**
  pattern: Command — make a request an object or a callable so it can be queued, logged, retried or undone.
  trigger: an action must be deferred to a worker, recorded, or run later from a queue.
  form: a Proc, an object with `call`, or a background job class; `method(:name)` when an existing method is the command.
  not when: the action runs immediately in the caller and nothing records it.

- **RB-P7 — Adapter.**
  pattern: Adapter — wrap an external API or a mismatched object behind the interface the app wants.
  trigger: a vendor SDK's names, return shapes or errors leak into domain code.
  form: a wrapper class, `SimpleDelegator` or `Forwardable`; tests stub the wrapper.
  not when: the third party is already behind the repo's own wrapper (see RB-12); reopening the class is the tempting, worse option (see RB-4).

- **RB-P8 — Proxy.**
  pattern: Proxy — stand in for another object to control when or how it is reached.
  trigger: a value is expensive to load, must be permission-checked, or comes from a remote system.
  form: `SimpleDelegator` or a small forwarding class that answers the same messages and defers the real work.
  not when: a direct call is cheap and the extra indirection buys nothing observable.

- **RB-P9 — Decorator.**
  pattern: Decorator — add behaviour per context without subclassing the object.
  trigger: view or boundary behaviour must wrap an existing object, often several wrappers deep.
  form: `SimpleDelegator` or `Module#prepend`; a decorator gem only when it is already a dependency (Draper, say).
  not when: the behaviour belongs in the object itself, or a single method override on the class would do.

- **RB-P10 — Singleton.**
  pattern: Singleton — exactly one instance, reachable everywhere.
  trigger: a process-wide collaborator must exist once and be shared.
  form: `include Singleton`, or more often a module with module methods or a constant.
  not when: tests would have to reach around it — inject the object instead; global singletons hurt tests.

- **RB-P11 — Factory.**
  pattern: Factory — build a concrete class the caller should not choose by hand.
  trigger: the class to build depends on input data or configuration.
  form: a class method (`Payment.for(type)`), a hash of classes, or `Object.const_get` over a checked allowlist.
  not when: the caller knows the class — plain `new` is the honest call.

- **RB-P12 — Builder.**
  pattern: Builder — construct a complex object in steps instead of one long argument list.
  trigger: construction has many optional parts, or the same steps must yield different representations.
  form: a fluent chain or an `instance_eval` DSL, as form builders and Nokogiri do.
  not when: keyword arguments with defaults cover the object — a builder for three options is ceremony.

- **RB-P13 — Interpreter.**
  pattern: Interpreter — evaluate sentences of a small language the domain defines.
  trigger: users or config express rules, formulas or filters that the app must evaluate.
  form: an internal DSL that lets Ruby be the interpreter, or a parser library (Parslet or Treetop) for a real grammar.
  not when: the "language" is a fixed set of options — case it out or use a lookup table.

- **RB-P14 — Block.**
  pattern: Block — a hook, callback or resource scope expressed as `yield`.
  trigger: a method must run caller code inside a scope it controls: open/close, around, retry.
  form: `yield` with an ensure, as `File.open { }` does; the block is Ruby's universal Strategy.
  not when: the caller's code must be stored and called later, out of scope — an object with `call` is clearer.

- **RB-P15 — Mixin.**
  pattern: Mixin — share behaviour across unrelated classes.
  trigger: several classes need the same methods but share no useful superclass.
  form: `include` for instance methods, `extend` for class methods, `prepend` to wrap; `Comparable` and `Enumerable` set the example.
  not when: the module is a fat class split across files, or the shared behaviour belongs to models (see RB-P27).

- **RB-P16 — Delegation.**
  pattern: Delegation — compose instead of inherit.
  trigger: an object must expose another's methods without becoming one of its subtypes.
  form: `Forwardable` and `def_delegators`, or Rails' `delegate`.
  not when: the object is genuinely a subtype — inheritance is the honest shape there.

- **RB-P17 — Null Object.**
  pattern: Null Object — a stand-in that answers the same messages as the real object.
  trigger: `nil` checks for a missing collaborator spread through the callers.
  form: a `NullUser` that answers the same message set as `User`, returning neutral values.
  not when: the absence is itself the error — raise or return a tagged result instead.

- **RB-P18 — Internal DSL.**
  pattern: Internal DSL — a declarative surface with the domain's own vocabulary.
  trigger: configuration or a domain description reads better than the code that implements it.
  form: `instance_eval` and `instance_exec`, method chaining, as RSpec, Rake and `routes.rb` do.
  not when: a plain method call or a hash of options is readable enough.

- **RB-P19 — Guard clause.**
  pattern: Guard clause — return early on the cases the method cannot serve.
  trigger: a method opens with nested conditionals and the happy path is buried.
  form: guard clauses at the top, inputs coerced once, and a raise for programmer errors.
  not when: the branches carry real behaviour — that is a State or Strategy decision, not a guard.

- **RB-P20 — Refinement.**
  pattern: Refinement — a scoped patch of an existing class.
  trigger: a class needs one method for one file's callers, not for the world.
  form: `refine` plus `using`; rare, and confined to the file that opts in.
  not when: the patch is not scoped to one file's callers — that is what RB-11 warns about.

- **RB-P21 — Value object.**
  pattern: Value object — an attribute with behaviour and equality, not a table row.
  trigger: a Rails app has a Money, Rating or Address that deserves methods and comparison.
  form: a plain Ruby object or `Data.define` (Ruby 3.2+), `Comparable`, and `freeze`.
  not when: a table-backed domain record is what is needed — see RB-9.

- **RB-P22 — Service object.**
  pattern: Service object — one action across several models or an external system.
  trigger: a Rails app runs a multi-step workflow with external side effects, and no single model owns it.
  form: one public `call`, named after the verb: `CompleteOrder.call(order)`.
  not when: the action is domain logic a model should own — see RB-16.

- **RB-P23 — Form object.**
  pattern: Form object — a form that spans models or maps to no table.
  trigger: a Rails app must validate and save input that does not fit one model.
  form: `ActiveModel::Model` with its own validations, then the writes it needs.
  not when: the form maps to one model — its own validations are the right place.

- **RB-P24 — Query object.**
  pattern: Query object — a query complex or reused enough to have a name.
  trigger: a Rails app builds the same query in several places, or a scope has outgrown a one-liner.
  form: `ActiveCustomersQuery.new(relation).call`, taking and returning a Relation so it composes.
  not when: a simple `scope` on the model reads just as well.

- **RB-P25 — Presenter.**
  pattern: Presenter — display logic that is not domain logic.
  trigger: a Rails app puts formatting and view decisions into helpers or the model.
  form: an object wrapping a model for one template.
  not when: the formatting is one `number_to_currency` call — keep it in the view.

- **RB-P26 — Policy.**
  pattern: Policy — "can this user do X?" as an object.
  trigger: a Rails app scatters permission checks through controllers and views.
  form: Pundit policies, or plain Ruby objects with a question-shaped method.
  not when: the check is a single owner comparison the model can answer itself.

- **RB-P27 — Concern.**
  pattern: Concern — behaviour shared by several models.
  trigger: a Rails app repeats the same methods in unrelated models.
  form: `ActiveSupport::Concern`.
  not when: the models diverge into subtypes — see RB-8; and a concern is not a place to split one fat model into files.

Canonical: Olsen, Design Patterns in Ruby; Metz, POODR; Helmkamp, 7 Patterns to Refactor Fat ActiveRecord Models.
