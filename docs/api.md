# REST contract v1

Base URL: `http://localhost:4000`. All `/v1` endpoints require bearer auth.
Success responses are unwrapped JSON (HTTP 200). Errors are
`{"error":{"code":"VALIDATION_ERROR","message":"..."}}`.
Authentication failures are 401, permission failures 403, inaccessible tenant
resources 404, state/policy conflicts 409, and invalid input 400.

Money uses positive integer USD cents (maximum 100,000,000); no caller-provided
currency or monetary overrides on commitment. Dates are ISO 8601 timestamps with
UTC/offset, such as `2027-01-15T12:00:00Z`. Strings are trimmed and limited to 8,000
characters, titles to 200. Resource IDs are opaque strings. Unknown input fields
are rejected. Delivery durations are integers from 1 through 90 days.

| Method | Path | Input / behavior |
| --- | --- | --- |
| GET | `/health` | Public `{status:"ok",mode:"demo"}` |
| GET | `/v1/me` | Principal ID, organization, kind, and role |
| GET | `/v1/listings?q=` | Active supplier listings, optional search |
| GET | `/v1/listings/:id` | One listing |
| GET | `/v1/sellers/:id/verification` | Simulated identity-check status |
| GET | `/v1/rfqs` | Buyer-owned or supplier-addressed RFQs |
| POST | `/v1/rfqs` | `{listingId,title,description,budgetCents,deadline,acceptanceCriteria}` |
| GET | `/v1/quotes?rfqId=` | Accessible quotes, optional RFQ filter |
| POST | `/v1/quotes` | Seller: `{rfqId,scope,totalCents,deliveryDays,expiresAt}` |
| PATCH | `/v1/quotes/:id` | Seller: same full payload; increments quote version |
| POST | `/v1/order-previews` | Buyer: `{quoteId}` |
| POST | `/v1/approvals` | Buyer: `{previewId}`; creates pending approval |
| GET | `/v1/approvals` | Buyer approvals with embedded preview for human review |
| POST | `/v1/approvals/:id/approve` | Human approver: `{}` |
| POST | `/v1/orders` | Buyer: `{previewId,approvalId}`, required `Idempotency-Key` (8–150 chars) |
| GET | `/v1/orders` | Buyer/seller scoped orders |
| GET | `/v1/orders/:id` | Buyer/seller scoped order |
| POST | `/v1/orders/:id/delivery` | Seller: `{url,notes}`, HTTPS URL only |
| POST | `/v1/orders/:id/accept` | Human buyer: `{}` |
| POST | `/v1/orders/:id/disputes` | Buyer: `{reason}` |
| GET | `/v1/audit` | Organization-scoped audit history |

`acceptanceCriteria` is one nonempty string. RFQ deadlines must be in the future.
Quote price must fit the RFQ budget; expiration must be future; delivery duration
must still fit before the RFQ deadline at preview and commit time. Each RFQ targets
one listing/supplier in this foundation. Buyers can compare separate requests.

## Main response shapes

- Listing: `id,sellerId,title,description,category,priceCents,currency,deliveryDays,verified`.
- RFQ: input plus `id,buyerId,currency,status,createdAt`.
- Quote: input plus `id,sellerId,currency,termsVersion,version,status`.
- Preview: `id,quoteId,buyerId,sellerId,scope,totalCents,currency,deliveryDays,
  termsVersion,quoteVersion,snapshotHash,expiresAt,requestedBy,acceptanceCriteria,
  deadline,termsText,feesCents,taxesCents`.
- Approval: `id,previewId,buyerId,status,payloadHash,requestedBy,approvedBy?,approvedAt?`.
- Order: `id,buyerId,sellerId,quoteId,previewId,approvalId,scope,totalCents,currency,
  status,paymentStatus,createdAt,committedBy,agentId?,contract,deliveryUrl?,
  deliveryNotes?,acceptedAt?`. `contract` is the complete approved preview snapshot.
  Order reads also include `disputes`, an array of recorded disputes with reasons,
  visible only to the buyer and supplier participating in that order.
- Dispute: `id,orderId,buyerId,sellerId,reason,status,createdAt`.
- Audit: `id,actorId,organizationId,action,resourceId,createdAt`.

States: RFQs `open → ordered`; quotes `offered → accepted`; approvals
`pending → approved → consumed`; orders `placed → delivered → accepted`, with
`disputed` reachable from each; payments `simulated_pending → simulated_settled`
on human acceptance. Disputes do not modify the payment status. There is no refund
or dispute-resolution implementation yet.

## Try discovery

```sh
curl http://localhost:4000/health
curl -H 'Authorization: Bearer demo-agent-token' http://localhost:4000/v1/listings
```

The frontend provides the full interactive buyer/seller workflow. The MCP CLI
provides equivalent narrow agent operations. Approval and acceptance are deliberately
human-only API operations. Agent callers cannot gain authority by adding an
`approved` field or supplying their own prices or terms at commit time.
