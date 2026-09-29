# Ruby on Rails best practices

Applied by the marestail `practices` judge to `*.rb`/`*.erb`/`*.html.erb` changes. Verified against Ruby 4.0, Rails 8.1, Hotwire (Turbo 8 + Stimulus + Hotwire Native), Solid Queue/Cache/Cable, Kamal 2 (September 2026). Hotwire, Active Record, and Rails routing rules apply only where that stack is present.

Ground rules: convention over configuration; rich models, slim controllers; broadcast from the model. RB-52 onwards come from the Ruby section of the design-patterns sheet (Olsen, Metz, Helmkamp, Grimm); where one of them and an earlier rule disagree, the earlier rule wins. Redis, Webpacker, Turbo Native, and generated system tests are gone from the default stack — do not cargo-cult them. Comments and YARD are out of scope (the comments gate forbids them).

## Convention, Ruby, OOP

- **RB-1 — stay on the rails.** Resourceful `resources :rooms` and conventional names. Bespoke `get "/fetchMessagesForRoom"` when REST would infer it is a finding.
- **RB-2 — don't write wiring Rails infers.** Custom inflections, creative directory layouts, and config that duplicates convention are taxes. Reconsider the design before overriding.
- **RB-3 — Ruby reads like the domain.** `user.posts.published.where("published_at > ?", 5.days.ago)`, `validates`, `with_lock`. A one-liner with six methods, three `&.`, and a ternary is a finding.
- **RB-4 — metaprogramming is a DSL, not a hiding place.** `belongs_to`, `broadcasts_to`, `enum`, `scope`, and concerns. No `method_missing`, no `define_method` from interpolated strings, no monkeypatching gems.
- **RB-5 — dynamic definitions are greppable.** `define_method` with a literal name the caller uses; not runtime string soup.
- **RB-6 — duck typing, not interfaces.** If it responds to `#each`, it is enumerable enough. No `IUserRepository`.
- **RB-7 — plug in by convention.** `to_param`, `to_partial_path` — `link_to` and `render @message` just work.
- **RB-8 — modules over STI.** Concerns for shared behaviour; STI/inheritance for diverging subtypes is a finding.
- **RB-9 — POROs for domain that is not a table.** `WeeklyReport` is a plain class, not an `ApplicationRecord`.
- **RB-10 — honor `private`.** `send` around privacy is a finding. Encapsulation is social; treat it as real.
- **RB-11 — do not monkeypatch stdlib or gems in app code.** Refinements (`refine` + `using`) or wrappers. Isolate a patch in one obvious file only as a last resort.
- **RB-12 — wrap external systems.** Domain talks to `PaymentGateway`, not Stripe. No DI ceremony; tests stub the wrapper.
- **RB-13 — SRP over fat models and fat controllers.** One job per object. "The file got big" is not a reason to extract a microservice.
- **RB-14 — majestic monolith.** Namespaces, concerns, engines inside one app. `has_many …, deprecated: true` to unwind coupling. Network boundaries need a scaling or team reason.

## Models & controllers

- **RB-15 — rich models, slim controllers.** Controller coordinates (`current_user.post_message(room:, body:)`); the model owns the workflow. Business rules in `create` actions are a finding.
- **RB-16 — no service-object junk drawer.** `app/services/` that re-implements the domain is a finding. A service is allowed only for a genuine multi-step workflow with external side effects (`Billing::SubscribeCustomer`).
- **RB-17 — callbacks for invariants only.** `before_validation :normalize_body`. Email, Slack, CRM, search index are jobs (`MessageDeliveryJob.perform_later`), not `after_save`.
- **RB-18 — scopes and modern AR.** `scope :recent`, `strict_loading`, `load_async`. Lazy N+1 loads that `strict_loading` would catch are a finding.
- **RB-19 — constraints are truth, validations are UX.** `add_foreign_key`, unique indexes, `t.references … foreign_key: true`. Validations without a matching DB constraint race.
- **RB-20 — no AR in views.** A partial that `Comment.where(...)` is an N+1 factory. Preload in the caller.
- **RB-21 — no `update_column` / `skip_callbacks` in app code.** Migrations and data-fixes only.

## Hotwire

These apply only to Rails views, models that broadcast, and Stimulus. Pick the lowest rung: Drive, Frames, morphing refreshes, Streams, Stimulus, then SPA.

- **RB-22 — Turbo Drive and Frames first.** Do not add a SPA or a stream for navigation and partial page updates Drive/Frames already do.
- **RB-23 — morphing + `broadcasts_refreshes` for "the page changed".** Turbo 8 page refresh is the default broadcast. Targeted streams only for high-frequency feeds.
- **RB-24 — `broadcasts_to` on the model.** `broadcasts_to ->(message) { [message.room, :messages] }`. Console, jobs, and API writes update UIs the same way. Broadcasting only from the controller is a finding.
- **RB-25 — subscribe in the view.** `turbo_stream_from @room, :messages` and `turbo_refreshes_with method: :morph, scroll: :preserve`. No custom JS websocket client for a CRUD live list.
- **RB-26 — controller stays boring.** `create` saves and returns; the model's broadcast does the rest.
- **RB-27 — Solid Cable by default.** Do not add Redis Action Cable without a connection-count reason in the task.
- **RB-28 — Stimulus for sprinkles.** Dropdowns and copy buttons. Not a client-side app.
- **RB-29 — SPA only for genuinely app-like surfaces.** A drag-and-drop editor, not a settings page.
- **RB-30 — Hotwire Native for mobile.** Bridge components wrap the web views. Do not start a second mobile codebase for the same UI.

## Testing

- **RB-31 — unit and integration first.** Models and domain in unit tests; controllers/API in integration tests (`assert_turbo_stream_broadcasts`). Do not add a system test for every feature.
- **RB-32 — a handful of system smokes.** Signup, checkout, the critical path. Rails 8.1 does not generate system tests; a large Capybara suite is a finding.
- **RB-33 — fixtures by default.** factory_bot only when the graph is gnarly. Parallel tests stay on.
- **RB-34 — mock external boundaries only.** VCR/WebMock at HTTP. Mocking the app's own collaborators re-tests the mocks.
- **RB-35 — `bin/ci` is the pipeline.** Tests, lint, Brakeman, bundler-audit. Do not invent a second CI script that diverges from `config/ci.rb`.

## Style, stack, ops

- **RB-36 — `rubocop-rails-omakase`.** The generated ruleset. Do not replace it with a personal RuboCop stack without a reason in the task.
- **RB-37 — modern Ruby.** Endless methods (`def archive! = update!(…)`), `it` in blocks (3.4+). Safe navigation without `&.` chains of death.
- **RB-38 — no types in app syntax.** RBS stays optional and out of `.rb` files. Clear names and tests, not Sorbet in a mainstream Rails app.
- **RB-39 — Propshaft + import maps.** No Webpacker, no Sprockets, no extra JS build pipeline in new apps.
- **RB-40 — Solid Queue / Cache / Cable first.** Do not add Redis or a job SaaS out of habit. Sidekiq only when pickup latency or throughput is the stated reason (Solid Queue polls, ~1–5s).
- **RB-41 — Kamal 2 + Thruster.** Default Dockerfile. Do not reach for Heroku/PaaS in new deploy config without a reason in the task.
- **RB-42 — YJIT on in production.** `RUBY_YJIT_ENABLE=1` in the generated Dockerfile. Turning it off is a finding. ZJIT is not production.
- **RB-43 — Puma defaults.** 3 threads (since 7.2). Do not bump to 5 from cargo-cult. Fibers/Falcon only for IO-heavy long-lived connections; Ractors are not production. Puma 8 binds IPv6 `::` by default; do not hard-code `0.0.0.0` around it.
- **RB-44 — Active Job Continuations for long jobs.** Resumable steps for Kamal's 30s shutdown. A long job that dies at deploy is a finding.
- **RB-45 — `config/ci.rb` + `bin/ci`.** Same checks locally and on GitHub Actions.
- **RB-46 — auth, mail, text from the stack.** Generated auth, Action Mailer, Action Text. Do not add a second auth/email/editor stack without a reason in the task.
- **RB-47 — `enum` and `scope` on the model.** Declarative AR, not hand-rolled status strings and class methods that duplicate them.
- **RB-48 — `with_lock` for check-then-act.** Incrementing counters and room invariants run in a lock, not a naked read/write race.
- **RB-49 — one test framework per repo.** Minitest or RSpec, whichever the repo already uses. A second one alongside it is a finding.
- **RB-50 — the usual supporting cast, not a second one.** `bullet` in development for N+1s, `test-prof` when the suite slows, Shoulda Matchers for one-line association and validation specs in RSpec. Adding a different tool for a job one of these already does is a finding.
- **RB-51 — Ruby 4.0 core.** `Set` is core: no `require "set"`. `array.rfind { … }`, not `array.reverse.find { … }`.

## Patterns

Most GoF patterns in Ruby are one block, one module, or one `SimpleDelegator` away. Reach for the lightest form; a class hierarchy for what a lambda does is a finding.

- **RB-52 — Strategy and Command are callables.** A lambda, `method(:name)`, or any object that responds to `call`. A class only when the strategy holds state. A one-method class per variant, picked with `case`, is a finding.
- **RB-53 — Template Method is rare.** A base class whose hooks raise `NotImplementedError` only when several subclasses share a real fixed sequence. A block or a strategy is the default.
- **RB-54 — collections are Enumerable.** A class that holds a collection defines `each` and `include Enumerable`; `.lazy` for large or streamed data. Handing callers the internal array to loop over is a finding.
- **RB-55 — wrap, don't reopen.** Adapter, Decorator, and Proxy use a wrapper class, `SimpleDelegator`, `Forwardable`/`def_delegators`, or Rails `delegate`. RB-4 still bans `method_missing` proxies. Draper only if the repo already has it.
- **RB-56 — Observer is `ActiveSupport::Notifications`.** Cross-cutting observation (metrics, audit, logging) subscribes to instrumented events. Business side effects stay explicit calls or jobs (RB-17).
- **RB-57 — no Singleton class.** A module with module methods, or a frozen constant. `include Singleton` and mutable class-level state are findings; they leak between tests.
- **RB-58 — Factory is a class method.** `Payment.for(type)` over a frozen hash of classes. `Object.const_get` or `constantize` on user input is a finding.
- **RB-59 — Builder is a chain.** Fluent calls that return `self` or a new value, or Relation composition. An `instance_eval` DSL only when the domain has its own vocabulary (routes, RSpec-like config).
- **RB-60 — duck types, not class checks.** Depend on messages. `is_a?`, `kind_of?`, or `case obj when SomeClass` in business logic is a finding; polymorphism or `respond_to?` at a boundary instead.
- **RB-61 — Null Object over repeated nil checks.** When the same `nil` branch appears in two or more places, a `NullUser` that answers the same messages replaces them.
- **RB-62 — blocks scope resources.** Open/close, lock/unlock, set/restore pairs take a block (`with_locale(:pl) { … }`) with `ensure` inside. Paired manual calls a caller can forget are a finding.
- **RB-63 — mixins for unrelated classes.** `include` for instance behaviour, `extend` for class behaviour, `prepend` to wrap; `Comparable` and `Enumerable` before hand-rolled operators. A concern that only splits one fat model into files is a finding.
- **RB-64 — confident code.** Guard clauses first, coerce inputs at the top, raise for programmer errors. `nil` checks threaded through the body and rescue-and-return-nil are findings.
- **RB-65 — value objects.** An attribute with behaviour and equality (money, amount + currency, address, date range) is `Data.define` or a frozen PORO, `Comparable` where it orders. Passing the same primitive pair through three methods is a finding.
- **RB-66 — service objects have one public `call`.** Named for the verb (`CompleteOrder.call(order)`), only when the action spans models or an external system (RB-16). A service that wraps one model method is a finding.
- **RB-67 — form objects.** A form that spans models or has no table is an `ActiveModel::Model` with its own validations. Conditional validations switched by a controller flag are a finding.
- **RB-68 — query objects compose.** A query too complex for a scope, or reused beyond one, is an object that takes a Relation and returns one (`ActiveCustomersQuery.new(relation).call`). Returning an Array from one is a finding.
- **RB-69 — presenters for display logic.** Formatting that is not domain logic lives in a view object that wraps one model for one template. Formatting methods on the model are a finding.
- **RB-70 — policy objects for "may this user do X?".** A plain policy object (or Pundit where the repo has it). The same role check repeated across controllers is a finding.
- **RB-71 — multi-step flows with rollback are one transaction in one service.** Do not add Interactor, dry-transaction, or Trailblazer without a reason in the task.
- **RB-72 — Sandi Metz's rules.** Classes up to 100 lines, method bodies up to 5 lines, at most 4 parameters (keyword arguments count), and one object handed from a controller action to its view. Break one only for a reason stated in the handoff.
- **RB-73 — depend on what changes less.** Inject collaborators as keyword defaults (`def initialize(gateway: PaymentGateway)`), not containers (RB-12). Isolate each external message in one small method so a change in that API touches one place.
