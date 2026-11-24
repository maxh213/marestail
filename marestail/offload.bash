# Set as BASH_ENV for every agent marestail starts, beside the shims first on PATH: a bash shell that puts another
# node or pnpm ahead of the shims (a login profile loading nvm, say) still sends test runs through them.
_marestail_shims="$(cd "$(dirname "${BASH_SOURCE[0]}")/shims" && pwd)"
for _marestail_tool in pnpm npm npx yarn vitest playwright jest stryker mutmut mix mvn mvnw; do
  eval "${_marestail_tool}() { \"\$_marestail_shims/${_marestail_tool}\" \"\$@\"; }"
done
unset _marestail_tool
