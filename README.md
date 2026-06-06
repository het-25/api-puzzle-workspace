# ⛏ Agent Mining Competition

A multiplayer AI agent competition set in Bengaluru. Your agent calls a remote API to explore a shared map, mine resources, and stack more wealth than everyone else.

## Getting started

### 1. Get your API key

Grab your team's API key from **Varun or Het** before you do anything else. You can't make a single API call without it.

### 2. Read the rules

Open [`docs/puzzle-rules.html`](docs/puzzle-rules.html) in your browser — it covers the full game mechanics, all tools, progression, capabilities, drinking rules, and the challenges.

You can also read it here: **https://github.com/het-25/api-puzzle-workspace/blob/main/docs/puzzle-rules.html**

> Tip: download the file and open it locally for the full styled experience. GitHub's HTML preview won't render it properly.

### 3. Get the tool schemas

Either hit the schema reference at **https://healthcare-gov-internal.vercel.app/** or ask Claude to enumerate the tool inputs for you — it knows them.

### 4. Build your agent and go

Your agent authenticates with:
```
Authorization: Bearer <your-api-key>
```

Every action is a `POST /tools/:name`. Move, mine, survey, teleport — whatever your strategy is, the API is the only interface. Good luck. ⛏
