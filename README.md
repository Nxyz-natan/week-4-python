⏺ # Flashcard Study API
  
  A flashcard study API with API key auth rate limiting and background tasks

  ## Features

  - **Decks** - create list view and delete flashcard decks 
  - **Cards** - add cards to a deck draw random due cards for studying
  - **Spaced repetition** - rate cards 1-4 to update their next due date using
  simple SM-2 lite math
  - **Rate limiting** - `/study` is limited to 10 requests/minute per API key
  other routes to 30/minute
  - **Background tasks** - rating a card logs the rating and recomputes deck
  stats after the response is sent
  - **SQLite storage** - three tables (api_keys decks cards) with foreign key
  cascading

  ## Screenshot

    ![alt text](https://file%2B.vscode-resource.vscode-cdn.net/Users/me/Desktop/Screenshot%202026-06-24%20at%203.36.59%E2%80%AFPM.png?version%3D1782337063541)