"""Upwork GraphQL queries — PORTED VERBATIM from v2
(etherwise_os/lib/upwork_api/queries.py, 2026-06-13). These strings encode
the hard-won API quirks (decision #14: knowledge carries as spec). Do not
edit field selections without re-reading docs/upwork-api-capabilities.md
in v2 — several omissions (e.g. latestStory sender) avoid non-null
violations on system stories.
"""
"""Canonical GraphQL queries used across Etherwise OS tasks.

Stored as module-level strings so they can be diffed in git, linted, and
referenced consistently. Pass `variables` to substitute placeholders when
the query template supports them.
"""

# ─────────────────────────────────────────────────────────────────────────
# Inbox — roomList for poll_inbox.py
# ─────────────────────────────────────────────────────────────────────────

ROOM_LIST = """
query RoomList($first: Int!, $after: String!) {
  roomList(
    filter: { subscribed_eq: true }
    pagination: { first: $first, after: $after }
    sortOrder: DESC
  ) {
    totalCount
    edges {
      node {
        id
        roomName
        topic
        roomType
        numUnread
        numUnreadMentions
        lastVisitedDateTime
        joinDateTime
        contractId
        contract {
          id
          title
          status
          contractType
          startDateTime
          endDateTime
          job { id content { title } }
        }
        vendorProposal {
          id
          coverLetter
          status { status }
          terms { chargeRate { rawValue currency displayValue } }
          marketplaceJobPosting {
            id
            content { title }
          }
          auditDetails {
            createdDateTime { rawValue }
            modifiedDateTime { rawValue }
          }
        }
        offerIds
        latestStory {
          id
          message
          createdDateTime
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


ROOM_STORIES = """
query RoomStories($roomId: ID!, $first: Int!) {
  room(id: $roomId) {
    id
    stories(filter: { pagination: { first: $first } }) {
      edges {
        node {
          id
          message
          createdDateTime
        }
      }
    }
  }
}
"""

# Stories WITH user/org subselection. Same caveat — Upwork bubbles up null
# on system stories where user is null.
# IMPORTANT: room.stories pagination must NOT include `after` on first page
# (unlike marketplaceJobPostings where it's required). Backend rejects it.
ROOM_STORIES_WITH_SENDERS = """
query RoomStoriesWithSenders($roomId: ID!, $first: Int!) {
  room(id: $roomId) {
    id
    stories(filter: { pagination: { first: $first } }) {
      edges {
        node {
          id
          message
          createdDateTime
          user { id name }
          organization { id name }
        }
      }
    }
  }
}
"""


# ─────────────────────────────────────────────────────────────────────────
# Marketplace — scanner queries
# ─────────────────────────────────────────────────────────────────────────

# Firehose: no search expression → all newly-posted jobs sorted by recency
MARKETPLACE_FIREHOSE = """
query MarketplaceFirehose($first: Int!, $after: String!) {
  marketplaceJobPostings(
    marketPlaceJobFilter: { pagination_eq: { first: $first, after: $after } }
    sortAttributes: [{ field: RECENCY }]
  ) {
    totalCount
    edges {
      node {
        id
        title
        description
        createdDateTime
        publishedDateTime
        totalApplicants
        applied
        category
        subcategory
        engagement
        durationLabel
        experienceLevel
        amount { rawValue currency displayValue }
        hourlyBudgetMin { rawValue currency }
        hourlyBudgetMax { rawValue currency }
        weeklyBudget { rawValue currency }
        client {
          companyName
          totalSpent { rawValue currency displayValue }
          totalHires
          totalFeedback
          totalReviews
          location { country city timezone }
          verificationStatus
        }
        skills { name prettyName }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


# OR-curated: keyword OR-set with n8n excluded, relevance-sorted
MARKETPLACE_CURATED = """
query MarketplaceCurated($expr: String!, $first: Int!, $after: String!) {
  marketplaceJobPostings(
    marketPlaceJobFilter: {
      searchExpression_eq: $expr
      verifiedPaymentOnly_eq: true
      pagination_eq: { first: $first, after: $after }
    }
    sortAttributes: [{ field: RELEVANCE }]
  ) {
    totalCount
    edges {
      node {
        id
        title
        description
        createdDateTime
        publishedDateTime
        totalApplicants
        applied
        category
        subcategory
        engagement
        durationLabel
        experienceLevel
        amount { rawValue currency displayValue }
        hourlyBudgetMin { rawValue currency }
        hourlyBudgetMax { rawValue currency }
        weeklyBudget { rawValue currency }
        client {
          companyName
          totalSpent { rawValue currency displayValue }
          totalHires
          totalFeedback
          totalReviews
          location { country city timezone }
          verificationStatus
        }
        skills { name prettyName }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


# Previous clients: clients we've worked with before
MARKETPLACE_PREVIOUS_CLIENTS = """
query MarketplacePreviousClients($first: Int!, $after: String!) {
  marketplaceJobPostings(
    marketPlaceJobFilter: {
      previousClients_eq: true
      pagination_eq: { first: $first, after: $after }
    }
    sortAttributes: [{ field: RECENCY }]
  ) {
    totalCount
    edges {
      node {
        id
        title
        description
        createdDateTime
        totalApplicants
        applied
        category
        subcategory
        engagement
        amount { rawValue currency displayValue }
        hourlyBudgetMin { rawValue currency }
        hourlyBudgetMax { rawValue currency }
        client {
          companyName
          totalSpent { rawValue currency displayValue }
          totalHires
          totalFeedback
          totalReviews
          location { country city }
          verificationStatus
        }
        skills { name prettyName }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


# Curated marketplace search — keyword-OR set covering Etherwise's target
# work, derived from the applied/interview job history (Make.com dominates,
# then Airtable / GoHighLevel / Zapier / Claude / Vapi, plus the recurring
# "AI automation / AI agent / workflow automation" phrasings). n8n stays
# excluded per the April 2026 step-back.
DEFAULT_CURATED_EXPRESSION = (
    '(make.com OR airtable OR gohighlevel OR "go high level" OR zapier '
    'OR vapi OR claude OR "ai automation" OR "ai agent" OR '
    '"workflow automation") -n8n'
)


# ─────────────────────────────────────────────────────────────────────────
# Offers — for state-sync
# ─────────────────────────────────────────────────────────────────────────

OFFER_BY_ID = """
query OfferById($id: ID!) {
  offer(id: $id) {
    id
    title
    description
    type
    state
    closeJobPostingOnAccept
    messageToContractor
    job { id content { title } }
    client { id name }
    clientCompany { name }
    vendorProposal { id }
  }
}
"""


# ─────────────────────────────────────────────────────────────────────────
# Transactions — dual-ACE for state-sync
# ─────────────────────────────────────────────────────────────────────────

TRANSACTION_HISTORY = """
query TransactionHistory($aceIds: [ID!]!, $startISO: String!, $endISO: String!) {
  transactionHistory(transactionHistoryFilter: {
    aceIds_any: $aceIds
    transactionDateTime_bt: {
      rangeStart: $startISO
      rangeEnd: $endISO
    }
  }) {
    transactionDetail {
      transactionHistoryRow {
        recordId
        type
        accountingSubtype
        description
        descriptionUI
        transactionAmount { rawValue currency displayValue }
        amountCreditedToUser { rawValue currency }
        transactionCreationDate
        fullyPaidDate
        assignmentDeveloperName
        assignmentCompanyName
        assignmentAgencyName
        assignmentTeamCompanyId
        relatedTransactionId
        paymentGuaranteed
        paymentStatus
      }
    }
  }
}
"""


# ─────────────────────────────────────────────────────────────────────────
# Whoami (token sanity)
# ─────────────────────────────────────────────────────────────────────────

WHOAMI = "{ user { id } }"
