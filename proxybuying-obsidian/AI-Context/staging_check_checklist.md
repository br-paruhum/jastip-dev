# Staging Integration Test Checklist

## Setup Requirements
* [ ] Open **Browser A** (e.g., Laptop Chrome) -> Logged in as **Buyer**
* [ ] Open **Browser B** (e.g., Laptop Firefox Incognito) -> Logged in as **Carrier 1**
* [ ] Open **Browser C** (e.g., Mobile Phone or Safari) -> Logged in as **Carrier 2**

---

## Test Phase 1: Cargo Posting & Multi-Carrier Offers
* [ ] **Buyer:** Post a new `Queuing Cargo` instance via the order creation page.
  * *Check:* Verify that the "Order Deadline" validation blocks any date under 7 days.
  * *Check:* Select "Reship package to Buyer's location" and verify the warning copy displays correctly underneath.
* [ ] **Carrier 1:** Navigate to the available cargo pool, select the Buyer's cargo, and submit a bid with an `Offer Rate`.
* [ ] **Carrier 2:** Access the *same* cargo instance and submit a separate competitive bid.
* [ ] **Buyer Dashboard (`My Orders`):** Refresh the page.
  * *Verification:* Do both Carrier 1 and Carrier 2 show up simultaneously in the data table?
  * *Verification:* Are both unique `Offer Rates` displayed clearly?

---

## Test Phase 2: The Acceptance Trigger & Database Isolation
* [ ] **Buyer:** Click "Accept" on **Carrier 1's** offer.
* [ ] **Database Transaction Validation:**
  * *Verification:* Does Carrier 1's status successfully transition to `Accepted`?
  * *Verification:* Does Carrier 2's offer automatically mutate to `Rejected` instantly?
  * *Verification:* Refresh **Carrier 2's Dashboard**. Ensure they see a rejected/closed status or that the offer has cleanly disappeared from their active bids according to your design specifications.

---

## Test Phase 3: Revised Payment Checkout
* [ ] **Buyer:** Proceed to the payment/escrow page for the accepted order.
  * *Verification:* Calculate the math manually. Is the checkout system charging exactly: `100% of Total Estimated Cost + Margin` AND `100% of Estimated Shipment Cost`?
  * *Verification:* Complete a dummy staging payment. Does the order transition smoothly to the next state without throwing a server error or database lock?

---

## Test Phase 4: Dynamic Interactive Dashboard States
* [ ] **Carrier 1 Dashboard (`?order=1#order-detail`):** 
  * *Verification:* Is the "Notes" tab closed by default?
  * *Verification:* Look under the Message tab header. Is the old string `"Private conversation...."` gone?
  * *Verification:* Is the "No message yet..." placeholder replaced with the long instruction text regarding sourcing issues?
* [ ] **Carrier 1 Dashboard (`?package_ready=1#package-ready`):**
  * *Verification:* Does *each individual line-item product row* display a photo upload button?
  * *Verification:* Is there a file upload button sitting right next to the "Actual Total Weight" field?
  * *Verification:* Is the Message tab forced into an expanded/open state on page load?
  * *Verification:* Upload an image. Check your backend/media storage or page source to verify it was automatically optimized and compressed into a `.webp` file.

---

## Test Phase 5: Document Engine & Asset Outputs
* [ ] **Carrier 1 Dashboard:** Upload the package box photos.
  * *Verification:* Switch to the Message tab. Are those uploaded box photos programmatically embedded directly inside the chat screen log automatically?
* [ ] **System PDF Generation:** Generate and download a `Customs Invoice`.
  * *Verification:* Open the file. Does the visual structure, layout alignment, and column balance perfectly replicate your target sample layout?
* [ ] **Static Assets:** Navigate directly to the new static `How-To` URL.
  * *Verification:* Do both structural images load instantly without rendering broken asset icons? Does the page follow your main layout theme?
