//
// Created by smher on 18-3-16.
//

#ifndef BACKEND_POSEGRAPH_H
#define BACKEND_POSEGRAPH_H

#include "system.h"


namespace myBackEnd
{
    //class PoseGraphEdge;

    class PoseGraph
    {
    public:
        typedef std::shared_ptr<PoseGraph> Ptr;

        ~PoseGraph();

        // 单例模式
        PoseGraph& createPG();

    private:
        PoseGraph();   // 单例模式
        //std::shared_ptr<PoseGraphEdge> edgePtr_;        // Has a Pose Graphic Edge

    };

    // 定义节点类型，基于李代数
    // 定义位姿图，基于G2O, 位姿的李代数表示的维度为：6， 类型为李代数: SE3
    // se3 in R^6 ! ! !
    // 主要定义： setToOriginImpl()以及oplusImpl()
    class PoseGraphVertex : public g2o::BaseVertex<6, Sophus::SE3>
    {
    public:
        EIGEN_MAKE_ALIGNED_OPERATOR_NEW

        PoseGraphVertex();

        bool read(std::istream& is);
        bool write(std::ostream& os) const;

        virtual void setToOriginImpl();
        virtual void oplusImpl(const double * update_);
    };

    // 定义边类型
    // 双节点边. error vector dimension: 6, measurement type: Sophus::SE3 or Eigen::Vector6d, VertexXi Type: PoseGraphVertex, VertexXj Type: PoseGraphVertex
    // 主要定义误差函数与Jacobin函数
    //class PoseGraphEdge : public g2o::BaseBinaryEdge<3, Sophus::SE3, PoseGraphVertex, PoseGraphVertex>
    class PoseGraphEdge : public g2o::BaseBinaryEdge<6, Sophus::SE3, PoseGraphVertex, PoseGraphVertex>
    {
    public:
        EIGEN_MAKE_ALIGNED_OPERATOR_NEW

        PoseGraphEdge();

        bool read(std::istream& is){}
        bool write(std::ostream& os) const {}

        virtual void linearizeOplus();
        void computeError();
    };

}

#endif //BACKEND_POSEGRAPH_H
