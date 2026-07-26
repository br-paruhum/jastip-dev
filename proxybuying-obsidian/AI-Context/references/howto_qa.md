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
A: goProxyBuy is a platform that facilitates:
1). Buyers buying goods abroad with the help of Proxy Buyers, and Travelers to carry the package to the buyer's location — shipment from the Proxy Buyer's location (Origin) to the buyer's location (Destination).
2). Buyers sending a package with the help of Travelers — shipment from the buyer's location (Origin) to the recipient location chosen by the buyer (Destination).

Q: I want to be a buyer, what should I do?
A: Register and confirm your email address. After logging in, select the buyer role, then verify your WhatsApp number.

Q: I want to be a proxy buyer, what should I do?
A: Start by filling in the [Contact](https://www.goproxybuy.com/contact/) page, stating your interest in becoming a Proxy Buyer. We will send you a form to fill in and sign before we add you to the Proxy Buyers List.

Q: I want to make money by carrying buyer's goods to the destination, what should I do?
A: Register and confirm your email address. After logging in, select the traveler role, then verify your WhatsApp number.

# Buyers

## 1. Buyers who want to buy goods abroad

Q: As a goods buyer, how do I start sending an order?
A: If there is a matching route and date, you can start sending an order through **Traveler's Offers List**; otherwise you can use the **Proxy Buyers List**.

Q: What should the buyer fill in before sending an order?
A: The buyer should fill in Product names (up to 10), Quantity, Deadline, Shipment option (Pick up the package at the traveler's location or Reship to the buyer's location), notes for the proxy buyer (if any), and upload product example photos (if any).

Q: What should the buyer do after they send an order?
A: The buyer needs to accept the Product cost estimate and Margin from the Proxy Buyer, and the shipment cost per kg from the Traveler.

Q: Can the buyer reject and negotiate the offers?
A: Yes, the buyer can reject and negotiate the offers. To reject, the buyer selects the Reject button; to negotiate, the buyer uses the Chat Box provided at the bottom of the page.

Q: What should the buyer do after accepting both the proxy buyer's estimate and the traveler's shipment cost?
A: The buyer will be asked to make the first deposit to the goProxyBuy account and wait for the goProxyBuy admin to verify the fund.

Q: What is next after the admin verified the buyer's first deposit fund?
A: The proxy buyer will start buying the ordered products, input the actual cost, upload product photos, make notes if there are any discrepancies between the buyer's order and the actual items (shortage, out-of-stock, substitution, price increase, etc. — the buyer should have given written consent for this in their Chat message), upload a box/luggage photo before it is closed, and click the Mark Package Ready button.

Q: What is next after the proxy buyer clicks the Mark Package Ready button?
A: The traveler is informed, and they will decide where and when the package will be handed over from the proxy buyer to the traveler.

Q: What is next after the package is handed over to the traveler?
A: The buyer will be informed by the platform when the traveler arrives at the destination and inputs the Customs Duty amount to be paid (if any).

Q: What should the buyer do after the traveler informs them about the amount of Customs Duty to be paid?
A: The buyer will be asked to make the second deposit to the goProxyBuy account, and the admin will verify the fund.

Q: What is next after the buyer makes the second deposit and the admin verifies the fund?
A: The traveler will pay the Customs Duty and upload the receipt.

Q: What is next after the traveler uploads the Customs Duty receipt?
A: It depends on the shipment option the buyer selected when creating the order.

Q: What is next if the buyer selected Pick up when creating the order?
A: The buyer should use the Chat Box to agree on when to visit the traveler's location.

Q: What is next after the buyer visits the traveler's location and reviews the package?
A: The buyer should click Package Received to close the transaction cycle.

Q: What is next if the buyer selected Reship the package when creating the order?
A: The buyer should send the traveler their preferred courier name and type, wait for the reshipment cost from the traveler, upload bank transfer proof for the reshipment cost reimbursement, and wait for the traveler to send the package and upload the Airway Bill.

Q: What is next after the buyer receives and reviews the package?
A: The buyer should click Package Received to close the transaction cycle.

## 2. Buyers who want to send the package abroad

Q: As a cargo buyer, how do I start sending an order?
A: If there is a matching route and date, you can start sending an order through **Traveler's Offers List**; otherwise you can **Create Cargo Order** through the Dashboard.

Q: What should the buyer fill in before sending an order?
A: The buyer should fill in Origin City and Country, Destination City and Country, Receiver Name and Address, Product names (up to 10), Quantity, Unit price of the product (for Customs purposes), Total weight, Order deadline, Delivery preference (Pick up or Reship), and upload product and box/luggage photos. This data will be displayed in the **Buyer's Cargo List**.

Q: What should the buyer do if a traveler sends an offer for their order?
A: The buyer needs to accept the shipment cost per kg from the Traveler. Please be aware that travelers have a minimum accepted weight. The buyer will pay the traveler's shipment cost per kg times the minimum accepted weight, even if the buyer's actual weight is less than the traveler's minimum weight.

Q: Can the buyer reject and negotiate the offers?
A: Yes, the buyer can reject and negotiate the offers. To reject, the buyer selects the Reject button; to negotiate, the buyer uses the Chat Box provided at the bottom of the page.

Q: What should the buyer do after accepting the traveler's shipment cost?
A: The buyer will be asked to make the first deposit to the goProxyBuy account and wait for the goProxyBuy admin to verify the fund.

Q: What should the buyer do next after the admin verified the buyer's first deposit fund?
A: The buyer can see the traveler's drop-off address and bring the package to that location on or before the latest drop-off date set by the platform. The traveler has the right to open and re-weigh (and re-measure the dimensions of) the package.

Q: What is next after the package is handed over to the traveler?
A: The buyer will be informed by the platform when the traveler arrives at the destination and inputs the Customs Duty amount to be paid (if any).

Q: What should the buyer do after the traveler informs them about the amount of Customs Duty to be paid?
A: The buyer will be asked to make the second deposit to the goProxyBuy account, and the admin will verify the fund.

Q: What is next after the buyer makes the second deposit and the admin verifies the fund?
A: The traveler will pay the Customs Duty and upload the receipt.

Q: What is next after the traveler uploads the Customs Duty receipt?
A: It depends on the shipment option the buyer selected when creating the order.

Q: What is next if the buyer selected Pick up when creating the order?
A: The buyer should use the Chat Box to agree on when to visit the traveler's location.

Q: What is next after the buyer visits the traveler's location and reviews the package?
A: The buyer should click Package Received to close the transaction cycle.

Q: What is next if the buyer selected Reship the package when creating the order?
A: The buyer should send the traveler their preferred courier name and type, wait for the reshipment cost from the traveler, upload bank transfer proof for the reshipment cost reimbursement, and wait for the traveler to send the package and upload the Airway Bill.

Q: What is next after the buyer receives and reviews the package?
A: The buyer should click Package Received to close the transaction cycle.

# Proxy Buyer

## Respond to Buyers who want to buy goods abroad

Q: What should the proxy buyer do when a buyer sends an order?
A: The proxy buyer should fill in the estimated cost of the products, the estimated total package weight, and the margin, then click Send Estimate.

Q: Can the proxy buyer edit the sent estimate?
A: Yes, the proxy buyer can edit it before the buyer accepts it.

Q: What should the proxy buyer do after the buyer accepts his/her estimate?
A: The proxy buyer should wait until the buyer also accepts the shipment cost from the traveler, makes the first deposit, and the fund is verified by the admin, before the proxy buyer can make any purchase.

Q: What should the proxy buyer do after the buyer makes the first deposit and the fund is verified by the admin?
A: The proxy buyer can start buying the ordered products, input the actual cost, upload product photos, make notes if there are any discrepancies between the buyer's order and the actual items (shortage, out-of-stock, substitution, price increase, etc. — the buyer should have given written consent for this in their Chat message), upload a box/luggage photo before it is closed, and click the Mark Package Ready button.

Q: What should the proxy buyer do after clicking Mark Package Ready?
A: The proxy buyer should use the Chat Box with the traveler to agree on the time and place to hand over the package, then hand the package over at the agreed time and place.

Q: When will the proxy buyer's payout be disbursed?
A: The proxy buyer's payout will be disbursed at most 24 hours after the buyer confirms receipt of the package.

# Traveler

## Respond to Buyers who want to buy goods abroad

Q: What should the traveler do to start making money from sharing their spare luggage space?
A: If there is a matching route and date, the traveler can send an Offer through **Buyer's Cargo List**; otherwise the traveler can **Create New Plan** from their dashboard. The data will be displayed on the **Traveler's Offers List**.

Q: What should the traveler do after sending an offer through **Buyer's Cargo List**?
A: Wait for the buyer to accept the traveler's offer, the buyer to make the first deposit, the admin to verify the fund, the proxy buyer to mark the package ready, and the proxy buyer to hand over the package to the traveler.

Q: What should the traveler do next after receiving the package from the proxy buyer?
A: The traveler should send the Customs duty amount when they arrive at the destination.

Q: What should the traveler do after sending the Customs duty amount at the destination?
A: Wait for the buyer to make the second deposit and the goProxyBuy admin to verify the fund.

Q: What should the traveler do after the goProxyBuy admin verifies the fund?
A: Pay the customs duty and upload the receipt.

Q: What should the traveler do after uploading the Customs duty receipt?
A: It depends on the shipment option the buyer selected when creating the order.

Q: What is next if the buyer selected Pick up when creating the order?
A: The buyer should use the Chat Box to agree on when to visit the traveler's location.

Q: What is next after the buyer visits the traveler's location and reviews the package?
A: The buyer should click Package Received to close the transaction cycle.

Q: What should the traveler do if the buyer selected Reship the package when creating the order?
A: The traveler should wait for the buyer to send their preferred courier name and type; then send the reshipment cost to the buyer; then wait for the buyer to upload bank transfer proof for the reshipment cost reimbursement; and then send the package and upload the Airway Bill.

Q: What is next for the traveler to do after the buyer receives and reviews the package?
A: The buyer should click Package Received to close the transaction cycle.

Q: When will the traveler's payout be disbursed?
A: The traveler's payout will be disbursed at most 24 hours after the buyer confirms receipt of the package.

## Respond to Buyers who want to send the package abroad

Q: What should the traveler do to start making money from sharing their spare luggage space?
A: If there is a matching route and date, the traveler can send an Offer through **Buyer's Cargo List**; otherwise the traveler can **Create New Plan** from their dashboard. The data will be displayed on the **Traveler's Offers List**.

Q: What should the traveler do after sending an offer through **Buyer's Cargo List**?
A: Wait for the buyer to accept the traveler's offer, the buyer to make the first deposit, the admin to verify the fund, and the buyer to drop off the package at the Drop-off location.

Q: What should the traveler do next after receiving the package at the Drop-off location?
A: The traveler should send the Customs duty amount when they arrive at the destination.

Q: What should the traveler do after sending the Customs duty amount at the destination?
A: Wait for the buyer to make the second deposit and the admin to verify the fund.

Q: What should the traveler do after the admin verifies the fund?
A: Pay the customs duty and upload the receipt.

Q: What should the traveler do after uploading the Customs duty receipt?
A: It depends on the shipment option the buyer selected when creating the order.

Q: What is next if the buyer selected Pick up when creating the order?
A: The buyer should use the Chat Box to agree on when to visit the traveler's location.

Q: What is next after the buyer visits the traveler's location and reviews the package?
A: The buyer should click Package Received to close the transaction cycle.

Q: What should the traveler do if the buyer selected Reship the package when creating the order?
A: The buyer should send the traveler their preferred courier name and type; then the traveler sends the reshipment cost to the buyer; then the buyer uploads bank transfer proof for the reshipment cost reimbursement; and then the buyer waits for the traveler to send the package and upload the Airway Bill.

Q: What is next for the traveler to do after the buyer receives and reviews the package?
A: The buyer should click Package Received to close the transaction cycle.

Q: When will the traveler's payout be disbursed?
A: The traveler's payout will be disbursed at most 24 hours after the buyer confirms receipt of the package.

## Contact

Q: How do I reach a human?
A: If your question isn't covered here, use the [Contact](https://www.goproxybuy.com/contact/) page.
