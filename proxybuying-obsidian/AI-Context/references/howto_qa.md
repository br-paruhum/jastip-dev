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
A: goProxyBuy is a platform that facilitate both 1). Buyer to buy goods abroad, 2). Proxy buyer to help buyer source and buy the goods and 3). Traveler to help buyer bring the goods to his/her location.

Q: I want to be a buyer, what should I do?
A: You have to registered and confirming your email address. After logged in and select as a buyer, you need to verify your WhatsApp number. 

Q: I want to be a proxy buyer, what should I do?
A: You can start by fill in [Contact](https://www.goproxybuy.com/contact/) page stating your interest in becoming a Proxy Buyer, we will send you a form to fill and sign before we start adding you to Proxy Buyers List.

Q: I want to make money by carrying buyer's goods to the destination, what should I do?
A: You need to register and confirming your email address. After logged in and select as a traveler, you need to verify your WhatsApp number. 

## Buyer

Q: As a buyer,  how to start sending an order?
A: After registered you can start sending an order either through **1. Queuing Traveler's Offers** table (if there is a match trip listed) or through **2. Proxy Buyers List** table if there is none in in table #1.
(placeholder) Summarise the buyer flow. See the How-To for Buyer page.

Q: What should the buyer filled in before sending an order?
A: The buyer should fill in Product Names (up to 10), quantity, deadline (if the buyer is sending through **2. Proxy Buyers List** table), Shipment option (Pick up the package to traveler location or Reship to the buyer location), notes for proxy buyer (if any), upload product example photo2 (if any).

Q: What should the buyer do after I send an order?
A: There will be two offers that the buyer need to accept. 1). Proxy buyer cost estimates, total package wight and margin. 2). Traveler shipment cost per kg.

Q: Can the buyer reject and negotiate the offers?
A: Yes the buyer can reject and negotiate the offers. To reject the buyer just select Reject button, to negotiate the buyer use Chat Box provided at the bottom of the page.

Q: What should the buyer do after accepting both proxy buyer's estimates and traveler's shipment cost?
A: The buyer will be asked to make first deposit to goProxyBuy account and wait for goProxyBuy admin to verify the fund.

Q: What is next proxy buyer do after admin verified buyer's first deposit fund?
A: Proxy buyer will start buying the buyer's ordered products, input actual cost, upload product photos, make any notes if there is any discrepancies between the buyer's order and the actual (shortage, out-of-stock, substitution, price increase, etc. -  the buyer should have a written consent regarding this in his/her Chat message), upload box/luggage photo before it closed and click Mark Package Ready button

Q: What is next after proxy buyer click Mark Package Ready button?
A: Traveler is informed and they will decide where and when the package will be handed over from proxy buyer to the traveler.

Q: What should the buyer do after the package was handed over to the traveler?
A: You will be informed by the platform when traveler arrived at the destination and inputing Customs Duty amount to be paid (if any).

Q: What should the buyer do after traveler inform me about the amount of Customs Duty tobe paid?
A: The buyer will be asked to make second deposit  to goProxyBuy account and admin will verify the fund.

Q: What is next after the buyer make the second deposit?
A: Traveler will pay the Customs Duty and upload the receipt.

Q: What is next after traveler upload Customs Duty receipt?
A: It depends on the buyer selected shipment option when the buyer create the order.

Q: What is next if the buyer selected Pick up when creating the order?
A: The buyer should use Chat Box to agreed on when to visit traveler location.

Q: What is next after the buyer visit traveler location and review the package?
A: The buyer should click Package Received to close the transaction cycle.

Q: What is next if the buyer selected Reship the package when creating the order?
A: The buyer should send to the traveler his/her preferred courier name and type, waiting for the reshipment cost from the traveler, the buyer uploading bank transfer proof for reshipment cost reimbursement and wait for the traveler to send the package and upload the Airway Bill.

Q: What is next after the buyer received and reviewed the package?
A: The buyer should click Package Received to close the transaction cycle.

## Proxy Buyer

Q: What should the proxy buyer do when buyer send me an order?
A: The proxy buyer should fill in the estimate cost of the products, estimate total package weight, margin and click Send Estimate.

Q: Can the proxy buyer edit the sent estimate?
A: Yes, the proxy buyer can edit it before the buyer accepted it.

Q: What should the proxy buyer do after buyer accept his/her estimate?
A: The proxy buyer should wait until buyer also accept the shipment cost from the traveler, make first deposit and fund verified by admin before the proxy buyer can make any purchase.

Q: What should the proxy buyer do after buyer made first deposit and fund verified by admin?
A: The proxy buyer can start buying the ordered products, input actual cost, upload product photos, make any notes if there is any discrepancies between the buyer's order and the actual (shortage, out-of-stock, substitution, price increase, etc. -  buyer should have a written consent regarding this in his/her Chat message), upload box/luggage photo before it closed and click Mark Package Ready button.

Q: What should the proxy buyer do after he/she clicked Mark Package Ready?
A: The proxy buyer should use Chat Box with the traveler to agreed on time and place to hand over the package and hand the package over on the agreed time and place.

Q: When will the proxy buyer payout disbursed?
A: The proxy buyer's payout will be disbursed maximum 24 hours after buyer confirmed received the package.

## Traveler

Q: What should the traveler do to start making money from sharing his/her spare luggage space??
A: After registered, the traveler can start send and Offer either through **3. Queuing Buyer's Cargo** table (if there is a match trip listed) or by creating new Travel Plan from the traveler's dashboard. The traveler's travel plan will be listed on  **1. Queuing Traveler's Offers** table.

Q: What should the traveler do after sending an offer through **3. Queuing Buyer's Cargo** table?
A: Wait for the buyer to accept the traveler's offer, buyer make first deposit, goProxyBuy admin verified the fund, proxy buyer mark package ready and proxy buyer hand over the package to the traveler.

Q: What should the traveler do next after collecting the package from the proxy buyer?
A: The traveler should send Customs duty amount when arived at the destination.

Q: What should the traveler do after sending Customs duty amount at the destination?
A: Waiting for buyer to make second deposit, goProxyBuy admin to verified the fund.

Q: What should the traveler do after goProxyBuy admin verified the fund?
A: Pay custom duty and upload the receipt.

Q: What should the traveler do after uploading the Customs duty receipt?
A: It depends on the buyer selected shipment option when the buyer create the order.

Q: What is next if the buyer selected Pick up when creating the order?
A: The buyer should use Chat Box to agreed on when to visit traveler location.

Q: What is next after the buyer visit traveler location and review the package?
A: The buyer should click Package Received to close the transaction cycle.

Q: What is next for the traveler todo if the buyer selected Reship the package when creating the order?
A: The buyer should send to the traveler his/her preferred courier name and type, the traveler send the reshipment cost to the buyer, the buyer uploading bank transfer proof for reshipment cost reimbursement and the buyer wait for the traveler to send the package and upload the Airway Bill.

Q: What is next for the traveler to do after the buyer received and reviewed the package?
A: The buyer should click Package Received to close the transaction cycle.

Q: When will the traveler payout disbursed?
A: The traveler's payout will be disbursed maximum 24 hours after buyer confirmed received the package.

## Contact

Q: How do I reach a human?
A: If your question isn't covered here, use the [Contact](https://www.goproxybuy.com/contact/) page.
