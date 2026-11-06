-- The FaaS case of the contract test: the starter's answers, given through a rock that the
-- dependency build installed from function-dev-1.rockspec (inspect), so the test fails when the
-- rock is not on LUA_PATH of the function's image.
local inspect = require("inspect")

local M = {}

function M.handler(event, context)
  print("query: " .. inspect(event.query))
  if event.trigger == "schedule" then
    return "tick from Lua"
  end
  if event.trigger == "queue" then
    local data = event.event and event.event.data
    return string.format("processed %s (from Lua)", type(data) == "table" and data.msg or event.body)
  end
  -- inspect("RunR") is "\"RunR\"": strip the quotes again to keep the starter's answer
  local name = inspect(event.query.name or "World"):sub(2, -2)
  return string.format("Hello, %s! (from Lua)", name)
end

return M
