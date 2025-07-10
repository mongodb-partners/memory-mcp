from typing import Any, Dict, List
from fastmcp import FastMCP
from bson import ObjectId
from services.mongodb_service import get_mongodb_service
from services.bedrock_service import get_bedrock_service
from services.external.tavily_service import search_web
from utils.serializers import serialize_mongodb_doc
from src.core.logger import logger

def register_search_tools(mcp: FastMCP):
    """Register search-related tools."""
    
    @mcp.tool(
        name="hybrid_search",
        description="""This tool provides all the necessary information required for the user's queries. 
                     This tool can Search MongoDB collections using advanced hybrid search algorithms that combine vector similarity and keyword matching.
        
        This tool executes a hybrid search on the user's MongoDB collections to find the most relevant documents based on both 
        semantic similarity (vector search) and keyword matching (full-text search). The results are weighted and combined to 
        provide comprehensive, contextually relevant information.
        """,
    )
    async def hybrid_search(
        connection_string: str,
        database_name: str,
        collection_name: str,
        fulltext_search_field: str,
        vector_search_index_name: str,
        vector_search_field: str,
        query: str,
        user_id: str,
        weight: float = 0.5,
        limit: int = 10,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Perform a hybrid search operation on MongoDB by combining full-text and vector (semantic) search results."""
        
        limit = int(limit)
        weight = float(weight)
        
        bedrock_service = get_bedrock_service()
        embedding = bedrock_service.generate_embeddings([query])[0]
        mongodb_service = get_mongodb_service(connection_string, database_name)
        collection = mongodb_service.get_collection(collection_name)

        pipeline = [
            {
                "$search": {
                    "index": fulltext_search_field,
                    "text": {"query": query, "path": fulltext_search_field},
                }
            },
            {"$match": {"metadata.user_id": user_id}},
            {"$addFields": {"fts_score": {"$meta": "searchScore"}}},
            {"$setWindowFields": {"output": {"maxScore": {"$max": "$fts_score"}}}},
            {
                "$addFields": {
                    "normalized_fts_score": {"$divide": ["$fts_score", "$maxScore"]}
                }
            },
            {
                "$project": {
                    "text": 1,
                    "normalized_fts_score": 1,
                }
            },
            {
                "$unionWith": {
                    "coll": collection_name,
                    "pipeline": [
                        {
                            "$vectorSearch": {
                                "index": vector_search_index_name,
                                "queryVector": embedding,
                                "path": vector_search_field,
                                "numCandidates": limit * 10,
                                "limit": limit,
                                "filter": {"metadata.user_id": user_id},
                            }
                        },
                        {"$addFields": {"vs_score": {"$meta": "vectorSearchScore"}}},
                        {
                            "$setWindowFields": {
                                "output": {"maxScore": {"$max": "$vs_score"}}
                            }
                        },
                        {
                            "$addFields": {
                                "normalized_vs_score": {
                                    "$divide": ["$vs_score", "$maxScore"]
                                }
                            }
                        },
                        {
                            "$project": {
                                "text": 1,
                                "normalized_vs_score": 1,
                            }
                        },
                    ],
                }
            },
            {
                "$group": {
                    "_id": "$_id",  # Group by document ID
                    "fts_score": {"$max": "$normalized_fts_score"},
                    "vs_score": {"$max": "$normalized_vs_score"},
                    "text_field": {"$first": "$text"},
                }
            },
            {
                "$addFields": {
                    "hybrid_score": {
                        "$add": [
                            {"$multiply": [weight, {"$ifNull": ["$vs_score", 0]}]},
                            {"$multiply": [1 - weight, {"$ifNull": ["$fts_score", 0]}]},
                        ]
                    }
                }
            },
            {"$sort": {"hybrid_score": -1}},  # Sort by combined hybrid score descending
            {"$limit": limit},  # Limit final output
            {
                "$project": {
                    "_id": 1,
                    "fts_score": 1,
                    "vs_score": 1,
                    "score": "$hybrid_score",
                    "text": "$text_field",
                }
            },
        ]

        # Execute the aggregation pipeline and return the results
        try:
            results = list(collection.aggregate(pipeline))
            # Serialize the results to handle ObjectId and other BSON types
            serialized_results = serialize_mongodb_doc(results)
            return {"results": serialized_results}
        except Exception as e:
            logger.error(f"Error in hybrid_search: {e}")
            raise

    @mcp.tool(name="search_web", description="Search Web using Tavily API")
    async def web_search_tool(query: str) -> List[str]:
        """Performs a web search using Tavily API."""
        return await search_web(query)
