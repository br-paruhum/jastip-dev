# goProxyBuy — Help Q&A (chatbot knowledge base)

This file is the ONLY source the help chatbot answers from. It is never shown to
users directly — it is injected into the chatbot's system prompt on every request.
Keep it in plain question/answer form. Aim to stay under ~10 A4 pages.

Editing tips:
- One clear question per "Q:", one concise answer per "A:".
- Group related questions under "## Section" headings for your own readability
  (the model reads the whole file, so headings are just for you).
- After editing, no restart is needed in DEBUG; in production restart the app
  so the file is re-read (it is cached at process start — see views.load_howto_qa).

---

## Getting started

Q: What is goProxyBuy?
A: (placeholder) Write a short description of the service here.

Q: How do I place an order as a buyer?
A: (placeholder) Summarise the buyer flow. See the How-To for Buyer page.

Q: How do I make an offer as a traveler?
A: (placeholder) Summarise the traveler flow. See the How-To for Traveler page.

## Payments

Q: When do I pay?
A: (placeholder)

Q: Is my payment secured?
A: (placeholder)

## Shipping & delivery

Q: How is my item delivered?
A: (placeholder)

## Contact

Q: How do I reach a human?
A: If your question isn't covered here, use the Contact page at /contact/.
